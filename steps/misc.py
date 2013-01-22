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

from twisted.python.failure import Failure, DefaultException
from twisted.internet import reactor
from twisted.spread.pb import PBConnectionLost
from twisted.python import log
from twisted.internet.defer import Deferred, TimeoutError

import os
import re

from buildbot.process.buildstep import LoggedRemoteCommand, BuildStep
from buildbot.steps.shell import WithProperties
from buildbot.status.builder import FAILURE, SUCCESS, worst_status
from buildbot.status.builder import STDOUT, STDERR  # MockProperty

from buildbotcustom.steps.base import LoggingBuildStep, ShellCommand, \
    addRetryEvaluateCommand, RetryingShellCommand
from buildbotcustom.common import genBuildID, genBuildUID
from buildbotcustom.try_parser import processMessage


class TinderboxShellCommand(ShellCommand):
    haltOnFailure = False

    """This step is really just a 'do not care' buildstep for executing a
       slave command and ignoring the results. If ignoreCodes is passed,
       only exit codes listed in it will be ignored. If ignoreCodes is not
       passed, all exit codes will be ignored.
    """
    def __init__(self, ignoreCodes=None, **kwargs):
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
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


class SendChangeStep(ShellCommand):
    warnOnFailure = True
    flunkOnFailure = False
    name = "sendchange"
    description = ["sendchange"]

    def __init__(self, master, branch, files, revision=None, user=None,
                 comments="", sendchange_props=None, timeout=1800, retries=5, **kwargs):

        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.addFactoryArguments(master=master, branch=branch, files=files,
                                 revision=revision, user=user,
                                 comments=comments, timeout=timeout,
                                 sendchange_props=sendchange_props, retries=retries)
        self.master = master
        self.branch = branch
        self.files = files
        self.revision = revision
        self.user = user
        self.comments = comments
        self.sendchange_props = sendchange_props or {}
        self.timeout = timeout
        self.retries = retries

        self.name = 'sendchange'

        self.sleepTime = 5

    def start(self):
        try:
            props = self.build.getProperties()
            branch = props.render(self.branch)
            revision = props.render(self.revision)
            comments = props.render(self.comments)
            files = props.render(self.files)
            user = props.render(self.user)
            sendchange_props = []
            for key, value in self.sendchange_props.items():
                sendchange_props.append((key, props.render(value)))

            self.addCompleteLog("sendchange", """\
    master: %s
    branch: %s
    revision: %s
    comments: %s
    user: %s
    files: %s
    properties: %s""" % (self.master, branch, revision, comments,
                         user, files, sendchange_props))
            bb_cmd = ['buildbot', 'sendchange', '--master', self.master,
                      '--username', user, '--branch', branch,
                      '--revision', revision]
            if isinstance(comments, basestring):
                if re.search('try: ', comments, re.MULTILINE):
                    comments = 'try: ' + ' '.join(processMessage(comments))
                else:
                    try:
                        comments = comments.splitlines()[0]
                    except IndexError:
                        comments = ''
                comments = re.sub(r'[\r\n^<>|;&"\'%$]', '_', comments)
                comments = comments.encode('ascii', 'replace')
                if comments:
                    bb_cmd.extend(['--comments', comments])

            for key, value in sendchange_props:
                bb_cmd.extend(['--property', '%s:%s' % (key, value)])

            if files:
                bb_cmd.extend(self.files)

            cmd = ['python',
                   WithProperties("%(toolsdir)s/buildfarm/utils/retry.py"),
                   '-s', str(self.sleepTime), '-t', str(self.timeout),
                   '-r', str(self.retries), '--stdout-regexp', 'change sent successfully']
            cmd.extend(bb_cmd)
            self.setCommand(cmd)
            self.super_class.start(self)
        except KeyError:
            self.addCompleteLog("errors", str(Failure()))
            return self.finished(FAILURE)


class DownloadFile(ShellCommand):
    haltOnFailure = True
    name = "download"
    description = ["download"]
    retries = 5
    waitRetry = 120

    def __init__(
        self, url_fn=None, url=None, url_property=None, filename_property=None,
            ignore_certs=False, wget_args=None, **kwargs):
        self.url = url
        self.url_fn = url_fn
        self.url_property = url_property
        self.filename_property = filename_property
        self.ignore_certs = ignore_certs
        assert bool(self.url) ^ bool(self.url_fn), \
            "One of url_fn or url must be set, not both (%s %s)"
        if wget_args:
            self.wget_args = wget_args[:]
        else:
            self.wget_args = ['--progress=dot:mega']
        self.wget_args += ["--tries=%d" % self.retries,
                           "--waitretry=%d" % self.waitRetry]
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.addFactoryArguments(url_fn=url_fn, url=url,
                                 url_property=url_property, filename_property=filename_property,
                                 ignore_certs=ignore_certs, wget_args=wget_args)

    def start(self):
        try:
            if self.url_fn:
                url = self.url_fn(self.build)
            else:
                url = self.url
        except Exception, e:
            self.addCompleteLog("errors", "Automation Error: %s" % str(e))
            return self.finished(FAILURE)

        renderedUrl = self.build.getProperties().render(url)
        if self.url_property:
            self.setProperty(self.url_property, renderedUrl, "DownloadFile")
        if self.filename_property:
            self.setProperty(self.filename_property,
                             os.path.basename(renderedUrl), "DownloadFile")

        if self.ignore_certs:
            self.setCommand(["wget"] + self.wget_args + ["-N",
                            "--no-check-certificate", url])
        else:
            self.setCommand(["wget"] + self.wget_args + ["-N", url])
        self.super_class.start(self)

    def evaluateCommand(self, cmd):
        superResult = self.super_class.evaluateCommand(self, cmd)
        if SUCCESS != superResult:
            return superResult
        if None != re.search('ERROR', cmd.logs['stdio'].getText()):
            return FAILURE
        return SUCCESS


class UnpackFile(ShellCommand):
    description = ["unpack"]

    def __init__(self, filename, scripts_dir=".", **kwargs):
        self.filename = filename
        self.scripts_dir = scripts_dir
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.addFactoryArguments(filename=filename, scripts_dir=scripts_dir)

    def start(self):
        filename = self.build.getProperties().render(self.filename)
        self.filename = filename
        if filename.endswith(".zip") or filename.endswith(".apk"):
            self.setCommand(['unzip', '-oq', filename])
        elif filename.endswith(".tar.gz"):
            self.setCommand(['tar', '-zxf', filename])
        elif filename.endswith(".tar.bz2"):
            self.setCommand(['tar', '-jxf', filename])
        elif filename.endswith(".dmg"):
            self.setCommand(['bash',
                             '%s/installdmg.sh' % self.scripts_dir,
                             filename]
                            )
        else:
            raise ValueError("Don't know how to handle %s" % filename)
        self.super_class.start(self)

    def evaluateCommand(self, cmd):
        superResult = self.super_class.evaluateCommand(self, cmd)
        if SUCCESS != superResult:
            return superResult
        if None != re.search('^Usage:', cmd.logs['stdio'].getText()):
            return FAILURE

        return SUCCESS


class UnpackTest(ShellCommand):
    description = ["unpack", "tests"]

    def __init__(self, filename, testtype, scripts_dir=".", **kwargs):
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.filename = filename
        self.scripts_dir = scripts_dir
        self.testtype = testtype
        self.addFactoryArguments(
            filename=filename, testtype=testtype, scripts_dir=scripts_dir)

    def start(self):
        filename = self.build.getProperties().render(self.filename)
        self.filename = filename
        if filename.endswith(".zip"):
            args = ['unzip', '-oq', filename, 'bin*', 'certs*', 'modules*']

            # modify the commands to extract only the files we need - the test
            # directory and bin/ and certs/
            if self.testtype == "mochitest":
                args.append('mochitest*')
            elif self.testtype == "xpcshell":
                args.append('xpcshell*')
            elif self.testtype == "jsreftest":
                # jsreftest needs both jsreftest/ and reftest/ in addition to
                # bin/ and certs/
                args.extend(['jsreftest*', 'reftest*'])
            elif self.testtype == "reftest":
                args.append('reftest*')
            elif self.testtype == "jetpack":
                args.append('jetpack*')
            else:
                # If it all fails, we extract the whole shebang
                args = ['unzip', '-oq', filename]

            self.setCommand(args)
        # If we come across a test not packaged as a zip file, try unpacking
        # the whole thing using tar+gzip/bzip2
        elif filename.endswith("tar.bz2"):
            self.setCommand(['tar', '-jxf', filename])
        elif filename.endswith("tar.gz"):
            self.setCommand(['tar', '-zxf', filename])
        else:
            # TODO: The test package is .zip across all three platforms, so
            # we're special casing for that
            raise ValueError("Don't know how to handle %s" % filename)
        self.super_class.start(self)

    def evaluateCommand(self, cmd):
        superResult = self.super_class.evaluateCommand(self, cmd)
        if superResult != SUCCESS:
            return superResult
        if None != re.search('^Usage:', cmd.logs['stdio'].getText()):
            return FAILURE

        return SUCCESS


class FindFile(ShellCommand):
    def __init__(self, filename, directory, max_depth, property_name, filetype=None, **kwargs):
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)

        self.addFactoryArguments(filename=filename, directory=directory,
                                 max_depth=max_depth, property_name=property_name,
                                 filetype=filetype)

        self.property_name = property_name

        if filetype == "file":
            filetype = "-type f"
        elif filetype == "dir":
            filetype = "-type d"
        else:
            filetype = ""

        self.setCommand(['bash', '-c', 'find %(directory)s -maxdepth %(max_depth)s %(filetype)s -name %(filename)s' % locals()])

    def evaluateCommand(self, cmd):
        worst = self.super_class.evaluateCommand(self, cmd)
        try:
            output = cmd.logs['stdio'].getText().strip()
            if output:
                self.setProperty(self.property_name, output)
                worst = worst_status(worst, SUCCESS)
            else:
                worst = worst_status(worst, FAILURE)
        except:
            pass
        return worst


class MozillaClobberer(ShellCommand):
    flunkOnFailure = False
    description = ['checking', 'clobber', 'times']

    def __init__(self, branch, clobber_url, clobberer_path, clobberTime=None,
                 timeout=3600, workdir='..', command=[], **kwargs):
        command = ['python', clobberer_path, '-s', 'tools']
        if clobberTime:
            command.extend(['-t', str(clobberTime)])

        command.extend([
            clobber_url,
            branch,
            WithProperties("%(buildername)s"),
            WithProperties("%(slavebuilddir)s"),
            WithProperties("%(slavename)s"),
            WithProperties("%(master)s"),
        ])

        self.super_class = ShellCommand

        self.super_class.__init__(self, command=command, timeout=timeout,
                                  workdir=workdir, **kwargs)

        self.addFactoryArguments(branch=branch, clobber_url=clobber_url,
                                 clobberer_path=clobberer_path,
                                 clobberTime=clobberTime)

    def setBuild(self, build):
        self.super_class.setBuild(self, build)
        # Set the "master" property
        master = build.builder.botmaster.parent.buildbotURL
        self.setProperty('master', master)

    def createSummary(self, log):
        my_builder = self.getProperty("builddir")
        # Server is forcing a clobber
        forcedClobberRe = re.compile(
            '%s:Server is forcing a clobber' % my_builder)
        # We are looking for something like :
        #  More than 604800.0 seconds have passed since our last clobber
        periodicClobberRe = re.compile('%s:More than [\d+\.]+ seconds have passed since our last clobber' % my_builder)

        # We don't have clobber data.  This usually means we've been purged
        # before
        purgedClobberRe = re.compile(
            "%s:Our last clobber date:.*None" % my_builder)

        self.setProperty('forced_clobber', False, 'MozillaClobberer')
        self.setProperty('periodic_clobber', False, 'MozillaClobberer')
        self.setProperty('purged_clobber', False, 'MozillaClobberer')

        clobberType = None
        for line in log.readlines():
            if forcedClobberRe.search(line):
                self.setProperty('forced_clobber', True, 'MozillaClobberer')
                clobberType = "forced"
            elif periodicClobberRe.search(line):
                self.setProperty('periodic_clobber', True, 'MozillaClobberer')
                clobberType = "periodic"
            elif purgedClobberRe.search(line):
                self.setProperty('purged_clobber', True, 'MozillaClobberer')
                clobberType = "free-space"

        if clobberType != None:
            summary = "TinderboxPrint: %s clobber" % clobberType
            self.addCompleteLog('clobberer', summary)


class SetBuildProperty(BuildStep):
    name = "set build property"

    def __init__(self, property_name, value, **kwargs):
        self.property_name = property_name
        self.value = value

        BuildStep.__init__(self, **kwargs)

        self.addFactoryArguments(property_name=property_name, value=value)

    def start(self):
        if callable(self.value):
            value = self.value(self.build)
        else:
            value = self.value
        self.setProperty(self.property_name, value)
        self.step_status.setText(['set props:', self.property_name])
        self.addCompleteLog(
            "property changes", "%s: %s" % (self.property_name, value))
        return self.finished(SUCCESS)


class OutputStep(BuildStep):
    """Simply logs some output"""
    name = "output"

    def __init__(self, data, log='output', **kwargs):
        self.data = data
        self.log = log

        BuildStep.__init__(self, **kwargs)

        self.addFactoryArguments(data=data, log=log)

    def start(self):
        properties = self.build.getProperties()
        if callable(self.data):
            data = properties.render(self.data(self.build))
        else:
            data = properties.render(self.data)
        if not isinstance(data, (str, unicode)):
            try:
                data = " ".join(data)
            except:
                data = str(data)
        self.addCompleteLog(self.log, data)
        self.step_status.setText([self.name])
        return self.finished(SUCCESS)


class DisconnectStep(ShellCommand):
    """This step is used when a command is expected to cause the slave to
    disconnect from the master.  It will handle connection lost errors as
    expected.

    Optionally it will also forcibly disconnect the slave from the master by
    calling the remote 'shutdown' command, in effect doing a graceful
    shutdown.  If force_disconnect is True, then the slave will always be
    disconnected after the command completes.  If force_disconnect is a
    function, it will be called with the command object, and the return value
    will be used to determine if the slave should be disconnected."""
    name = "disconnect"

    def __init__(self, force_disconnect=None, **kwargs):
        self.force_disconnect = force_disconnect
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.addFactoryArguments(force_disconnect=force_disconnect)

        self._disconnected = False
        self._deferred_death = None

    def interrupt(self, reason):
        # Called when the slave command is interrupted, e.g. by rebooting
        # We assume this is expected
        self._disconnected = True
        return self.finished(SUCCESS)

    def start(self):
        # Give the machine 60 seconds to go away on its own
        def die():
            self._deferred_death = None
            log.msg("Forcibly disconnecting %s" % self.getSlaveName())
            self.buildslave.disconnect()
        self._deferred_death = reactor.callLater(60, die)
        return self.super_class.start(self)

    def checkDisconnect(self, f):
        # This is called if there's a problem executing the command because the connection was disconnected.
        # Again, we assume this is the expected behaviour
        f.trap(PBConnectionLost)
        self._disconnected = True
        return self.finished(SUCCESS)

    def commandComplete(self, cmd):
        # The command has completed normally.  If force_disconnect is set, then
        # tell the slave to shutdown
        if self.force_disconnect:
            if not callable(self.force_disconnect) or self.force_disconnect(cmd):
                try:
                    d = self.remote.callRemote('shutdown')
                    d.addErrback(self._disconnected_cb)
                    d.addCallback(self._disconnected_cb)
                    return d
                except:
                    log.err()
                    return

        # Otherwise, cancel our execution
        if self._deferred_death and self._deferred_death.active:
            self._deferred_death.cancel()
            self._deferred_death = None

    def _disconnected_cb(self, res):
        # Successfully disconnected
        self._disconnected = True
        return True

    def finished(self, res):
        if self._disconnected:
            self.step_status.setText(self.describe(True) + ["slave", "lost"])
            self.step_status.setText2(['slave', 'lost'])
            if self._deferred_death and self._deferred_death.active:
                self._deferred_death.cancel()
                self._deferred_death = None
        return self.super_class.finished(self, res)


class RepackPartners(ShellCommand):
    '''This step allows a regular ShellCommand to be optionally extended
       based on provided properties. This is useful for tweaking the command
       to be run based on, e.g., properties supplied by the user in the
       force builds web interface.
    '''
    def __init__(self, **kwargs):
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)

    def start(self):
        try:
            properties = self.build.getProperties()
            if 'partner' in properties:
                partner = properties['partner']
                self.command.extend(['-p', partner])
        except:
            # No partner was specified, so repacking all partners.
            pass
        self.super_class.start(self)


class FunctionalStep(BuildStep):
    name = "functional_step"

    def __init__(self, func, **kwargs):
        self.func = func

        BuildStep.__init__(self, **kwargs)

        self.addFactoryArguments(func=func)

    def start(self):
        result = self.func(self, self.build)
        return self.finished(result)


def setBuildIDProps(step, build):
    """Sets buildid and builduid properties.

    On a rebuild we willl re-generate the builduid.  Otherwise, we normally get
    them from the scheduler.

    If either of buildid or builduid doesn't exist, it will be created."""

    if build.reason.startswith("The web-page 'rebuild'"):
        # Override builduid since this is a manually triggered
        # rebuild
        build.setProperty("builduid", genBuildUID(), "setBuildProps")

    # Make sure we have required properties
    props = build.getProperties()
    if "buildid" not in props:
        build.setProperty("buildid", genBuildID(), "setBuildProps")
    if "builduid" not in props:
        build.setProperty("builduid", genBuildUID(), "setBuildProps")

    return SUCCESS
