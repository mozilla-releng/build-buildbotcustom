# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla-specific Buildbot steps.
#
# The Initial Developer of the Original Code is
# Mozilla Corporation.
# Portions created by the Initial Developer are Copyright (C) 2007
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Axel Hecht <axel@mozilla.com>
# ***** END LICENSE BLOCK *****
from twisted.python import log
import logging
from logging import DEBUG

class LogFwd(object):
    @classmethod
    def write(cls, msg):
        log.msg(msg.rstrip())
        pass
    @classmethod
    def flush(cls):
        pass

def init(**kw):
    logging.basicConfig(stream = LogFwd,
                        format = '%(name)s: (%(levelname)s) %(message)s')
    for k, v in kw.iteritems():
        logging.getLogger(k).setLevel(v)

def critical(cat, msg):
    logging.getLogger(cat).critical(msg)
    pass

def error(cat, msg):
    logging.getLogger(cat).error(msg)
    pass

def warning(cat, msg):
    logging.getLogger(cat).warning(msg)
    pass

def info(cat, msg):
    logging.getLogger(cat).info(msg)
    pass

def debug(cat, msg):
    logging.getLogger(cat).debug(msg)
    pass
