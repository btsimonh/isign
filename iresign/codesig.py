from abc import ABCMeta
import construct
import hashlib
import macho_cs
import OpenSSL


# See the documentation for an explanation of how
# CodeDirectory slots work.
class CodeDirectorySlot(object):
    __metaclass__ = ABCMeta
    offset = None

    def __init__(self, codesig):
        self.codesig = codesig

    def get_hash(self):
        return hashlib.sha1(self.get_contents()).digest()


class EntitlementsSlot(CodeDirectorySlot):
    offset = -5

    def get_contents(self):
        return self.codesig.get_blob_data('CSMAGIC_ENTITLEMENT')


class ApplicationSlot(CodeDirectorySlot):
    offset = -4

    def get_contents(self):
        return 0


class ResourceDirSlot(CodeDirectorySlot):
    offset = -3

    def __init__(self, seal_path):
        self.seal_path = seal_path

    def get_contents(self):
        return open(self.seal_path, "rb").read()


class RequirementsSlot(CodeDirectorySlot):
    offset = -2

    def get_contents(self):
        return self.codesig.get_blob_data('CSMAGIC_REQUIREMENTS')


class InfoSlot(CodeDirectorySlot):
    offset = -1

    def get_contents(self):
        # this will probably be similar to ResourceDir slot,
        # a hash of file contents
        raise "unimplemented"


#
# Represents a code signature object, aka the LC_CODE_SIGNATURE,
# within the Signable
#
class Codesig(object):
    """ wrapper around construct for code signature """
    def __init__(self, signable, data):
        self.signable = signable
        self.construct = macho_cs.Blob.parse(data)

    def build_data(self):
        return macho_cs.Blob.build(self.construct)

    def get_blob(self, magic):
        for index in self.construct.data.BlobIndex:
            if index.blob.magic == magic:
                return index.blob
        raise KeyError(magic)

    def get_blob_data(self, magic):
        """ convenience method, if we just want the data """
        blob = self.get_blob(magic)
        return macho_cs.Blob_.build(blob)

    def set_entitlements(self, entitlements_path):
        print "entitlements:"
        entitlements_data = None
        try:
            entitlements = self.get_blob('CSMAGIC_ENTITLEMENT')
        except KeyError:
            print "no entitlements found"
        else:
            # make entitlements data if slot was found
            # libraries do not have entitlements data
            # so this is actually a difference between libs and apps
            entitlements_data = macho_cs.Blob_.build(entitlements)
            print hashlib.sha1(entitlements_data).hexdigest()

            entitlements.bytes = open(entitlements_path, "rb").read()
            entitlements.length = len(entitlements.bytes) + 8
            entitlements_data = macho_cs.Blob_.build(entitlements)
            print hashlib.sha1(entitlements_data).hexdigest()

        print

    def set_requirements(self, signer):
        print "requirements:"
        requirements = self.get_blob('CSMAGIC_REQUIREMENTS')
        requirements_data = macho_cs.Blob_.build(requirements)
        print hashlib.sha1(requirements_data).hexdigest()

        # read in our cert, and get our Common Name
        signer_key_data = open(signer.signer_key_file, "rb").read()
        signer_p12 = OpenSSL.crypto.load_pkcs12(signer_key_data)
        subject = signer_p12.get_certificate().get_subject()
        signer_cn = dict(subject.get_components())['CN']

        # this is for convenience, a reference to the first blob
        # structure within requirements, which contains the data
        # we are going to change
        req_blob_0 = requirements.data.BlobIndex[0].blob
        req_blob_0_original_length = req_blob_0.length

        try:
            cn = req_blob_0.data.expr.data[1].data[1].data[0].data[2].Data
        except Exception:
            print "no signer CN rule found in requirements"
            print requirements
        else:
            # if we could find a signer CN rule, make requirements.

            # first, replace old signer CN with our own
            cn.data = signer_cn
            cn.length = len(cn.data)

            # req_blob_0 contains that CN, so rebuild it, and get what
            # the length is now
            req_blob_0.bytes = macho_cs.Requirement.build(req_blob_0.data)
            req_blob_0.length = len(req_blob_0.bytes) + 8

            # fix offsets of later blobs in requirements
            offset_delta = req_blob_0.length - req_blob_0_original_length
            for bi in requirements.data.BlobIndex[1:]:
                bi.offset += offset_delta

            # rebuild requirements, and set length for whole thing
            requirements.bytes = macho_cs.Entitlements.build(requirements.data)
            requirements.length = len(requirements.bytes) + 8

        # then rebuild the whole data, but just to show the digest...?
        requirements_data = macho_cs.Blob_.build(requirements)
        print hashlib.sha1(requirements_data).hexdigest()
        print

    def get_codedirectory(self):
        return self.get_blob('CSMAGIC_CODEDIRECTORY')

    def get_codedirectory_hash_index(self, slot):
        """ The slots have negative offsets, because they start from the 'top'.
            So to get the actual index, we add it to the length of the
            slots. """
        return slot.offset + self.get_codedirectory().data.nSpecialSlots

    def has_codedirectory_slot(self, slot):
        """ Some dylibs have all 5 slots, even though technically they only need
            the first 2. If this dylib only has 2 slots, some of the calculated
            indices for slots will be negative. This means we don't do
            those slots when resigning (for dylibs, they don't add any
            security anyway) """
        return self.get_codedirectory_hash_index(slot) >= 0

    def fill_codedirectory_slot(self, slot):
        if self.signable.should_fill_slot(slot):
            index = self.get_codedirectory_hash_index(slot)
            self.get_codedirectory().data.hashes[index] = slot.get_hash()

    def set_codedirectory(self, seal_path, signer):
        if self.has_codedirectory_slot(EntitlementsSlot):
            self.fill_codedirectory_slot(EntitlementsSlot(self))

        if self.has_codedirectory_slot(ResourceDirSlot):
            self.fill_codedirectory_slot(ResourceDirSlot(seal_path))

        if self.has_codedirectory_slot(RequirementsSlot):
            self.fill_codedirectory_slot(RequirementsSlot(self))

        cd = self.get_codedirectory()
        cd.data.teamID = signer.team_id

        cd.bytes = macho_cs.CodeDirectory.build(cd.data)
        cd_data = macho_cs.Blob_.build(cd)
        print len(cd_data)
        # open("cdrip", "wb").write(cd_data)
        print "CDHash:", hashlib.sha1(cd_data).hexdigest()
        print

    def set_signature(self, signer):
        # TODO how do we even know this blobwrapper contains the signature?
        # seems like this is a coincidence of the structure, where
        # it's the only blobwrapper at that level...
        print "sig:"
        sigwrapper = self.get_blob('CSMAGIC_BLOBWRAPPER')
        oldsig = sigwrapper.bytes.value
        # signer._print_parsed_asn1(sigwrapper.data.data.value)
        # open("sigrip.der", "wb").write(sigwrapper.data.data.value)
        cd_data = self.get_blob_data('CSMAGIC_CODEDIRECTORY')
        sig = signer.sign(cd_data)
        print "sig len:", len(sig)
        print "old sig len:", len(oldsig)
        # open("my_sigrip.der", "wb").write(sig)
        # print hexdump(oldsig)
        sigwrapper.data = construct.Container(data=sig)
        # signer._print_parsed_asn1(sig)
        # sigwrapper.data = construct.Container(data="hahaha")
        sigwrapper.length = len(sigwrapper.data.data) + 8
        sigwrapper.bytes = sigwrapper.data.data
        # print len(sigwrapper.bytes)
        # print hexdump(sigwrapper.bytes)
        print

    def update_offsets(self):
        # update section offsets, to account for any length changes
        offset = self.construct.data.BlobIndex[0].offset
        for blob in self.construct.data.BlobIndex:
            blob.offset = offset
            offset += len(macho_cs.Blob.build(blob.blob))

        superblob = macho_cs.SuperBlob.build(self.construct.data)
        self.construct.length = len(superblob) + 8
        self.construct.bytes = superblob

    def resign(self, app, signer):
        self.set_entitlements(app.entitlements_path)
        self.set_requirements(signer)
        self.set_codedirectory(app.seal_path, signer)
        self.set_signature(signer)
        self.update_offsets()

    # TODO make this optional, in case we want to check hashes or something
    # print hashes
    # cd = codesig_cons.data.BlobIndex[0].blob.data
    # end_offset = arch_macho.macho_start + cd.codeLimit
    # start_offset = ((end_offset + 0xfff) & ~0xfff) - (cd.nCodeSlots * 0x1000)

    # for i in xrange(cd.nSpecialSlots):
    #    expected = cd.hashes[i]
    #    print "special exp=%s" % expected.encode('hex')

    # for i in xrange(cd.nCodeSlots):
    #     expected = cd.hashes[cd.nSpecialSlots + i]
    #     f.seek(start_offset + 0x1000 * i)
    #     actual_data = f.read(min(0x1000, end_offset - f.tell()))
    #     actual = hashlib.sha1(actual_data).digest()
    #     print '[%s] exp=%s act=%s' % (
    #         ('bad', 'ok ')[expected == actual],
    #         expected.encode('hex'),
    #         actual.encode('hex')
    #     )