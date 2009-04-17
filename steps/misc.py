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
#   Ben Hearsum <bhearsum@mozilla.com>
#   Rob Campbell <rcampbell@mozilla.com>
#   Chris Cooper <ccooper@mozilla.com>
# ***** END LICENSE BLOCK *****

from twisted.python.failure import Failure

import buildbot
import re
from buildbot.process.buildstep import LoggedRemoteCommand, LoggingBuildStep, \
  BuildStep
from buildbot.steps.shell import ShellCommand, WithProperties
from buildbot.status.builder import FAILURE, SUCCESS
from buildbot.clients.sendchange import Sender

class CreateDir(ShellCommand):
    name = "create dir"
    haltOnFailure = False
    warnOnFailure = True

    def __init__(self, platform, dir=None, **kwargs):
        ShellCommand.__init__(self, **kwargs)
        self.addFactoryArguments(platform=platform, dir=dir)
        self.platform = platform
        if dir:
            self.dir = dir
        else:
            if self.platform.startswith('win'):
                self.command = r'if not exist ' + self.dir + r' mkdir ' + \
                               self.dir
            else:
                self.command = ['mkdir', '-p', self.dir]

class TinderboxShellCommand(ShellCommand):
    haltOnFailure = False
    
    """This step is really just a 'do not care' buildstep for executing a
       slave command and ignoring the results. If ignoreCodes is passed,
       only exit codes listed in it will be ignored. If ignoreCodes is not
       passed, all exit codes will be ignored.
    """
    def __init__(self, ignoreCodes=None, **kwargs):
       ShellCommand.__init__(self, **kwargs)
       self.addFactoryArguments(ignoreCodes=ignoreCodes)
       self.ignoreCodes = ignoreCodes
    
    def evaluateCommand(self, cmd):
       # Ignore all return codes
       if not self.ignoreCodes:
          return SUCCESS
       else:
          # Ignore any of the return codes we're told to
          if cmd.rc in self.ignoreCodes:
             return SUCCESS
          # If the return code is something else, fail
          else:
             return FAILURE

class GetHgRevision(ShellCommand):
    """Retrieves the revision from a Mercurial repository. Builds based on
    comm-central use this to query the revision from mozilla-central which is
    pulled in via client.py, so the revision of the platform can be displayed
    in addition to the comm-central revision we get through got_revision.
    """
    name = "get hg revision"
    command = ["hg", "identify", "-i"]

    def commandComplete(self, cmd):
        rev = ""
        try:
            rev = cmd.logs['stdio'].getText().strip().rstrip()
            # Locally modified ?
            mod = rev.find('+')
            if mod != -1:
                rev = rev[:mod]
                self.setProperty('hg_modified', True)
            self.setProperty('hg_revision', rev)
        except:
            log.msg("Could not find hg revision")
            log.msg("Output: %s" % rev)
            return FAILURE
        return SUCCESS

class GetBuildID(ShellCommand):
    """Retrieves the BuildID from a Mozilla tree (using platform.ini) and sets
    it as a build property ('buildid'). If defined, uses objdir as it's base.
    """
    description=['getting buildid']
    descriptionDone=['get buildid']
    haltOnFailure=True

    def __init__(self, objdir="", inifile="application.ini", section="App",
            **kwargs):
        ShellCommand.__init__(self, **kwargs)
        self.addFactoryArguments(objdir=objdir,
                                 inifile=inifile,
                                 section=section)

        self.objdir = objdir
        self.command = ['python', 'config/printconfigsetting.py',
                        '%s/dist/bin/%s' % (self.objdir, inifile),
                        section, 'BuildID']

    def commandComplete(self, cmd):
        buildid = ""
        try:
            buildid = cmd.logs['stdio'].getText().strip().rstrip()
            self.setProperty('buildid', buildid)
        except:
            log.msg("Could not find BuildID or BuildID invalid")
            log.msg("Found: %s" % buildid)
            return FAILURE
        return SUCCESS


class SetMozillaBuildProperties(LoggingBuildStep):
    """Gathers and sets build properties for the following data:
      buildid - BuildID of the build (from application.ini, falling back on
       platform.ini)
      appVersion - The version of the application (from application.ini, falling
       back on platform.ini)
      packageFilename - The filename of the application package
      packageSize - The size (in bytes) of the application package
      packageHash - The sha1 hash of the application package
      installerFilename - The filename of the installer (win32 only)
      installerSize - The size (in bytes) of the installer (win32 only)
      installerHash - The sha1 hash of the installer (win32 only)
      completeMarFilename - The filename of the complete update
      completeMarSize - The size (in bytes) of the complete update
      completeMarHash - The sha1 hash of the complete update

      All of these will be set as build properties -- even if no data is found
      for them. When no data is found, the value of the property will be None.

      This function requires an argument of 'objdir', which is the path to the
      objdir relative to the builddir. ie, 'mozilla/fx-objdir'.
    """

    def __init__(self, objdir="", **kwargs):
        LoggingBuildStep.__init__(self, **kwargs)
        self.addFactoryArguments(objdir=objdir)
        self.objdir = objdir

    def describe(self, done=False):
        if done:
            return ["gather", "build", "properties"]
        else:
            return ["gathering", "build", "properties"]

    def start(self):
        args = {'objdir': self.objdir, 'timeout': 60}
        cmd = LoggedRemoteCommand("setMozillaBuildProperties", args)
        self.startCommand(cmd)

    def evaluateCommand(self, cmd):
        # set all of the data as build properties
        # some of this may come in with the value 'UNKNOWN' - these will still
        # be set as build properties but 'UNKNOWN' will be substituted with None
        try:
            log = cmd.logs['stdio'].getText()
            for property in log.split("\n"):
                name, value = property.split(": ")
                if value == "UNKNOWN":
                    value = None
                self.setProperty(name, value)
        except:
            return FAILURE
        return SUCCESS

class SendChangeStep(BuildStep):
    warnOnFailure = True
    def __init__(self, master, branch, files, revision=None, user=None,
            comments="", **kwargs):
        BuildStep.__init__(self, **kwargs)
        self.addFactoryArguments(master=master, branch=branch, files=files,
                revision=revision, user=user, comments=comments)
        self.master = master
        self.branch = branch
        self.files = files
        self.revision = revision
        self.user = user
        self.comments = comments

        self.name = 'sendchange'

        self.sender = Sender(master)

    def start(self):
        properties = self.build.getProperties()

        master = self.master
        try:
            branch = properties.render(self.branch)
            revision = properties.render(self.revision)
            comments = properties.render(self.comments)
            files = properties.render(self.files)
            user = properties.render(self.user)

            self.addCompleteLog("sendchange", """\
    master: %(master)s
    branch: %(branch)s
    revision: %(revision)s
    comments: %(comments)s
    user: %(user)s
    files: %(files)s""" % locals())
        except KeyError:
            return self.finished(Failure())

        d = self.sender.send(branch, revision, comments, files, user)

        d.addCallbacks(self.finished, self.finished)
        return d

    def finished(self, results):
        if results is None:
            self.step_status.setText(['sendchange to', self.master, 'ok'])
            return BuildStep.finished(self, SUCCESS)
        else:
            self.step_status.setText(['sendchange to', self.master, 'failed'])
            if self.warnOnFailure:
                self.step_status.setText2(['sendchange'])
            self.addCompleteLog("errors", str(results))
            return BuildStep.finished(self, FAILURE)



class MozillaClobberer(ShellCommand):
    flunkOnFailure = False
    description=['checking', 'clobber', 'times']

    def __init__(self, branch, clobber_url, clobberer_path, clobberTime=None,
                 timeout=3600, workdir='.', **kwargs):
        command = ['python', clobberer_path, '-s', 'tools']
        if clobberTime:
            command.extend(['-t', str(clobberTime)])
        command.extend([clobber_url, branch, WithProperties("%(buildername)s"),
                        WithProperties("%(slavename)s")])

        ShellCommand.__init__(self, command=command, timeout=timeout,
                              workdir=workdir, **kwargs)
        self.addFactoryArguments(branch=branch, clobber_url=clobber_url,
                                 clobberer_path=clobberer_path,
                                 clobberTime=clobberTime)

    def createSummary(self, log):

        # Server is forcing a clobber
        forcedClobberRe = re.compile('Server is forcing a clobber')
        # We are looking for something like :
        #  More than 7 days, 0:00:00 have passed since our last clobber
        periodicClobberRe = re.compile('More than \d+ days, [0-9:]+ have passed since our last clobber')

        self.setProperty('forced_clobber', False, 'MozillaClobberer')
        self.setProperty('periodic_clobber', False, 'MozillaClobberer')

        clobberType = None
        for line in log.readlines():
            if forcedClobberRe.search(line):
                self.setProperty('forced_clobber', True, 'MozillaClobberer')
                clobberType = "forced"
            if periodicClobberRe.search(line):
                self.setProperty('periodic_clobber', True, 'MozillaClobberer')
                clobberType = "periodic"

        if clobberType != None:
            summary = "TinderboxPrint: %s clobber" % clobberType
            self.addCompleteLog('clobberer', summary)

