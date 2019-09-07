#!/bin/bash

# extract the signature binary from the end of the app, save as applsig.cms, then run this to get a dump of the content that would need to be reproduced.

openssl cms -print -inform DER -cmsout -in applsig.cms


