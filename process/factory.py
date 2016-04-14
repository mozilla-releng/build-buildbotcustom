from __future__ import absolute_import

import os.path
import re

from twisted.python import log

from buildbot.process.buildstep import regex_log_evaluator
from buildbot.process.factory import BuildFactory
from buildbot.steps.shell import WithProperties
from buildbot.steps.transfer import FileDownload, JSONPropertiesDownload, \
    JSONStringDownload
from buildbot.steps.dummy import Dummy
from buildbot import locks
from buildbot.status.builder import worst_status
from buildbot.util import json

import buildbotcustom.common
import buildbotcustom.status.errors
import buildbotcustom.steps.base
import buildbotcustom.steps.misc
import buildbotcustom.steps.source
import buildbotcustom.steps.test
import buildbotcustom.steps.unittest
import buildbotcustom.steps.signing
import buildbotcustom.steps.mock
import buildbotcustom.env
import buildbotcustom.misc_scheduler
import build.paths
import release.info
import release.paths
reload(buildbotcustom.status.errors)
reload(buildbotcustom.steps.base)
reload(buildbotcustom.steps.misc)
reload(buildbotcustom.steps.source)
reload(buildbotcustom.steps.test)
reload(buildbotcustom.steps.unittest)
reload(buildbotcustom.steps.signing)
reload(buildbotcustom.steps.mock)
reload(buildbotcustom.env)
reload(build.paths)
reload(release.info)
reload(release.paths)

from buildbotcustom.status.errors import purge_error, global_errors, \
    upload_errors
from buildbotcustom.steps.base import ShellCommand, SetProperty, Mercurial, \
    Trigger, RetryingShellCommand
from buildbotcustom.steps.misc import TinderboxShellCommand, SendChangeStep, \
    MozillaClobberer, FindFile, DownloadFile, UnpackFile, \
    SetBuildProperty, DisconnectStep, OutputStep, \
    RepackPartners, FunctionalStep, setBuildIDProps
from buildbotcustom.steps.source import MercurialCloneCommand
from buildbotcustom.steps.test import GraphServerPost
from buildbotcustom.steps.signing import SigningServerAuthenication
from buildbotcustom.common import getSupportedPlatforms, getPlatformFtpDir, \
    genBuildID, normalizeName, getPreviousVersion
from buildbotcustom.steps.mock import MockReset, MockInit, MockCommand, \
    MockInstall, MockMozillaCheck, MockProperty, RetryingMockProperty, \
    RetryingMockCommand

import buildbotcustom.steps.unittest as unittest_steps

from buildbot.status.builder import SUCCESS, FAILURE, EXCEPTION, RETRY

from release.paths import makeCandidatesDir

# limit the number of clones of the try repository so that we don't kill
# dm-vcview04 if the master is restarted, or there is a large number of pushes
hg_try_lock = locks.MasterLock("hg_try_lock", maxCount=20)

hg_l10n_lock = locks.MasterLock("hg_l10n_lock", maxCount=20)

# Limit ourselves to uploading 4 things at a time
upload_lock = locks.MasterLock("upload_lock", maxCount=10)

SIGNING_SERVER_CERT = os.path.join(
    os.path.dirname(build.paths.__file__),
    '../../../release/signing/host.cert')


class DummyFactory(BuildFactory):
    def __init__(self, delay=5, triggers=None):
        BuildFactory.__init__(self)
        self.addStep(Dummy(delay))
        if triggers:
            self.addStep(Trigger(
                schedulerNames=triggers,
                waitForFinish=False,
            ))


def makeDummyBuilder(name, slaves, category=None, delay=0, triggers=None,
                     properties=None, env=None):
    properties = properties or {}
    builder = {
        'name': name,
        'factory': DummyFactory(delay, triggers),
        'builddir': name,
        'slavenames': slaves,
        'properties': properties,
    }
    if category:
        builder['category'] = category
    if env:
        builder['env'] = env.copy()
    return builder


def postUploadCmdPrefix(upload_dir=None,
                        branch=None,
                        product=None,
                        revision=None,
                        version=None,
                        who=None,
                        builddir=None,
                        buildid=None,
                        buildNumber=None,
                        to_tinderbox_dated=False,
                        to_tinderbox_builds=False,
                        to_dated=False,
                        to_latest=False,
                        to_try=False,
                        to_candidates=False,
                        to_mobile_candidates=False,
                        nightly_dir=None,
                        as_list=True,
                        signed=False,
                        log=False,
                        bucket_prefix=None,
                        ):
    """Returns a post_upload.py command line for the given arguments.

    If as_list is True (the default), the command line will be returned as a
    list of arguments.  Some arguments may be WithProperties instances.

    If as_list is False, the command will be returned as a WithProperties
    instance representing the entire command line as a single string.

    It is expected that the returned value is augmented with the list of files
    to upload, and where to upload it.
    """

    cmd = ["post_upload.py"]

    if upload_dir:
        cmd.extend(["--tinderbox-builds-dir", upload_dir])
    if branch:
        cmd.extend(["-b", branch])
    if product:
        cmd.extend(["-p", product.lower()])
    if buildid:
        cmd.extend(['-i', buildid])
    if buildNumber:
        cmd.extend(['-n', buildNumber])
    if version:
        cmd.extend(['-v', version])
    if revision:
        cmd.extend(['--revision', revision])
    if who:
        cmd.extend(['--who', who])
    if builddir:
        cmd.extend(['--builddir', builddir])
    if to_tinderbox_dated:
        cmd.append('--release-to-tinderbox-dated-builds')
        if not log:
            cmd.append('--release-to-latest-tinderbox-builds')
    if to_tinderbox_builds:
        cmd.append('--release-to-tinderbox-builds')
    if to_try:
        cmd.append('--release-to-try-builds')
    if to_latest:
        cmd.append("--release-to-latest")
    if to_dated:
        cmd.append("--release-to-dated")
    if to_candidates:
        cmd.append("--release-to-candidates-dir")
    if to_mobile_candidates:
        cmd.append("--release-to-mobile-candidates-dir")
    if nightly_dir:
        cmd.append("--nightly-dir=%s" % nightly_dir)
    if signed:
        cmd.append("--signed")
    if bucket_prefix:
        cmd.extend(['--bucket-prefix', bucket_prefix])

    if as_list:
        return cmd
    else:
        # Remove WithProperties instances and replace them with their fmtstring
        for i, a in enumerate(cmd):
            if isinstance(a, WithProperties):
                cmd[i] = a.fmtstring
        return WithProperties(' '.join(cmd))


def parse_make_upload(rc, stdout, stderr):
    ''' This function takes the output and return code from running
    the upload make target and returns a dictionary of important
    file urls.'''
    retval = {}
    for m in re.findall(
        "^(https?://.*?\.(?:tar\.bz2|dmg|zip|apk|mar|tar\.gz))$",
            "\n".join([stdout, stderr]).replace('\r\n', '\n'), re.M):
        if m.endswith("crashreporter-symbols.zip"):
            retval['symbolsUrl'] = m
        elif 'mozharness' in m and m.endswith('.zip'):
            pass
        elif m.endswith("crashreporter-symbols-full.zip"):
            retval['symbolsUrl'] = m
        elif m.endswith("tests.tar.bz2") or m.endswith("tests.zip"):
            retval['testsUrl'] = m
        elif m.endswith('apk') and 'unsigned-unaligned' in m:
            retval['unsignedApkUrl'] = m
        elif m.endswith('apk') and 'robocop' in m:
            retval['robocopApkUrl'] = m
        elif 'jsshell-' in m and m.endswith('.zip'):
            retval['jsshellUrl'] = m
        elif m.endswith('.complete.mar'):
            retval['completeMarUrl'] = m
        elif m.endswith('.mar') and '.partial.' in m:
            retval['partialMarUrl'] = m
        elif '.sdk.' in m:
            retval['sdkUrl'] = m
        elif m.endswith('bouncer.apk'):
            pass
        elif m.find('geckoview') >= 0:
            pass
        elif m.find('cppunit') >= 0:
            pass
        else:
            retval['packageUrl'] = m
    return retval


def short_hash(rc, stdout, stderr):
    ''' This function takes an hg changeset id and returns just the first 12 chars'''
    retval = {}
    retval['got_revision'] = stdout[:12]
    return retval


def get_signing_cmd(signingServers, python):
    if not python:
        python = 'python'
    cmd = [
        python,
        '%(toolsdir)s/release/signing/signtool.py',
        '--cachedir', '%(basedir)s/signing_cache',
        '-t', '%(basedir)s/token',
        '-n', '%(basedir)s/nonce',
        '-c', '%(toolsdir)s/release/signing/host.cert',
    ]
    for ss, user, passwd, formats in signingServers:
        opt = "%s:%s" % (":".join(formats), ss)
        cmd.extend(['-H', opt])
    return ' '.join(cmd)


def getPlatformMinidumpPath(platform):
    platform_minidump_path = {
        'linux': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
        'linuxqt': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
        'linux64': WithProperties('%(toolsdir:-)s/breakpad/linux64/minidump_stackwalk'),
        'win32': WithProperties('%(toolsdir:-)s/breakpad/win32/minidump_stackwalk.exe'),
        'win64': WithProperties('%(toolsdir:-)s/breakpad/win64/minidump_stackwalk.exe'),
        'macosx': WithProperties('%(toolsdir:-)s/breakpad/osx/minidump_stackwalk'),
        'macosx64': WithProperties('%(toolsdir:-)s/breakpad/osx64/minidump_stackwalk'),
        # Android uses OSX *and* Linux because the Foopies are on both.
        'android': WithProperties('/builds/minidump_stackwalk'),
        'android-x86': WithProperties('/builds/minidump_stackwalk'),
        'android-armv6': WithProperties('/builds/minidump_stackwalk'),
    }
    return platform_minidump_path[platform]


class RequestSortingBuildFactory(BuildFactory):
    """Base class used for sorting build requests according to buildid.

    In most cases the buildid of the request is calculated at the time when the
    build is scheduled.  For tests, the buildid of the sendchange corresponds
    to the buildid of the build. Sorting the test requests by buildid allows us
    to order them according to the order in which the builds were scheduled.
    This avoids the problem where build A of revision 1 completes (and triggers
    tests) after build B of revision 2. Without the explicit sorting done here,
    the test requests would be sorted [r2, r1], and buildbot would choose the
    latest of the set to run.

    We sort according to the following criteria:
        * If the request looks like a rebuild, use the request's submission time
        * If the request or any of the changes contains a 'buildid' property,
          use the greatest of these property values
        * Otherwise use the request's submission time
    """
    def newBuild(self, requests):
        def sortkey(request):
            # Ignore any buildids if we're rebuilding
            # Catch things like "The web-page 'rebuild' ...", or self-serve
            # messages, "Rebuilt by ..."
            if 'rebuil' in request.reason.lower():
                return int(genBuildID(request.submittedAt))

            buildids = []

            props = [request.properties] + [
                c.properties for c in request.source.changes]

            for p in props:
                try:
                    buildids.append(int(p['buildid']))
                except:
                    pass

            if buildids:
                return max(buildids)
            return int(genBuildID(request.submittedAt))

        try:
            sorted_requests = sorted(requests, key=sortkey)
            return BuildFactory.newBuild(self, sorted_requests)
        except:
            # Something blew up!
            # Return the orginal list
            log.msg("Error sorting build requests")
            log.err()
            return BuildFactory.newBuild(self, requests)


class MockMixin(object):
    warnOnFailure = True
    warnOnWarnings = True

    def addMockSteps(self):
        # do not add the steps more than once per instance
        if (hasattr(self, '_MockMixin_added_mock_steps')):
            return
        self._MockMixin_added_mock_steps = 1

        if self.mock_copyin_files:
            for source, target in self.mock_copyin_files:
                self.addStep(ShellCommand(
                    name='mock_copyin_%s' % source.replace('/', '_'),
                    command=['mock_mozilla', '-r', self.mock_target,
                             '--copyin', source, target],
                    haltOnFailure=True,
                ))
                self.addStep(MockCommand(
                    name='mock_chown_%s' % target.replace('/', '_'),
                    command='chown -R mock_mozilla %s' % target,
                    target=self.mock_target,
                    mock=True,
                    workdir='/',
                    mock_args=[],
                    mock_workdir_prefix=None,
                ))
        # This is needed for the builds to start
        self.addStep(MockCommand(
            name='mock_mkdir_basedir',
            command=WithProperties(
                "mkdir -p %(basedir)s" + "/%s" % self.baseWorkDir),
            target=self.mock_target,
            mock=True,
            workdir='/',
            mock_workdir_prefix=None,
        ))
        if self.use_mock and self.mock_packages:
            self.addStep(MockInstall(
                target=self.mock_target,
                packages=self.mock_packages,
                timeout=2700,
            ))


class TooltoolMixin(object):
    def addTooltoolStep(self, **kwargs):
        command = [
            'sh',
            WithProperties(
                '%(toolsdir)s/scripts/tooltool/tooltool_wrapper.sh'),
            self.tooltool_manifest_src,
            self.tooltool_url_list[0],
            self.tooltool_bootstrap,
        ]
        if self.tooltool_script:
            command.extend(self.tooltool_script)

        # include relengapi authentication information
        relengapi_tok = '/builds/relengapi.tok'
        if self.platform.startswith('win'):
            relengapi_tok = r'c:\builds\relengapi.tok'
        command.extend(['--authentication-file', relengapi_tok])

        self.addStep(MockCommand(
            name='run_tooltool',
            command=command,
            env=self.env,
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
            **kwargs
        ))


class MozillaBuildFactory(RequestSortingBuildFactory, MockMixin):
    ignore_dirs = ['info', 'rel-*:10d', 'tb-rel-*:10d']

    def __init__(self, hgHost, repoPath, buildToolsRepoPath, buildSpace=0,
                 clobberURL=None, clobberBranch=None, clobberTime=None,
                 buildsBeforeReboot=None, branchName=None, baseWorkDir='build',
                 hashType='sha512', baseMirrorUrls=None, baseBundleUrls=None,
                 signingServers=None, enableSigning=True, env={},
                 balrog_api_root=None, balrog_credentials_file=None,
                 balrog_submitter_extra_args=None, balrog_username=None,
                 use_mock=False, mock_target=None, mock_packages=None,
                 mock_copyin_files=None, enable_pymake=False, **kwargs):
        BuildFactory.__init__(self, **kwargs)

        if hgHost.endswith('/'):
            hgHost = hgHost.rstrip('/')
        self.hgHost = hgHost
        self.repoPath = repoPath
        self.buildToolsRepoPath = buildToolsRepoPath
        self.buildToolsRepo = self.getRepository(buildToolsRepoPath)
        self.buildSpace = buildSpace
        self.clobberURL = clobberURL
        self.clobberTime = clobberTime
        self.buildsBeforeReboot = buildsBeforeReboot
        self.baseWorkDir = baseWorkDir
        self.hashType = hashType
        self.baseMirrorUrls = baseMirrorUrls
        self.baseBundleUrls = baseBundleUrls
        self.signingServers = signingServers
        self.enableSigning = enableSigning
        self.env = env.copy()
        self.balrog_api_root = balrog_api_root
        self.balrog_credentials_file = balrog_credentials_file
        self.balrog_username = balrog_username
        self.balrog_submitter_extra_args = balrog_submitter_extra_args
        self.use_mock = use_mock
        self.mock_target = mock_target
        self.mock_packages = mock_packages
        self.mock_copyin_files = mock_copyin_files
        self.enable_pymake = enable_pymake

        self.repository = self.getRepository(repoPath)
        if branchName:
            self.branchName = branchName
        else:
            self.branchName = self.getRepoName(self.repository)
        if not clobberBranch:
            self.clobberBranch = self.branchName
        else:
            self.clobberBranch = clobberBranch

        if self.enable_pymake:
            self.makeCmd = ['python', WithProperties("%(basedir)s/build/build/pymake/make.py")]
        else:
            self.makeCmd = ['make']

        if self.signingServers and self.enableSigning:
            self.signing_command = get_signing_cmd(
                self.signingServers, self.env.get('PYTHON26'))
            self.env['MOZ_SIGN_CMD'] = WithProperties(self.signing_command)

        # Make sure the objdir is specified with an absolute path
        if 'MOZ_OBJDIR' in self.env and not os.path.isabs(self.env['MOZ_OBJDIR']):
            self.env['MOZ_OBJDIR'] = WithProperties('%(basedir)s' + '/%s/%s' % (self.baseWorkDir, self.env['MOZ_OBJDIR']))

        self.addInitialSteps()

    def addInitialSteps(self):
        self.addStep(SetProperty(
            name='set_basedir',
            command=['bash', '-c', 'pwd'],
            property='basedir',
            workdir='.',
        ))
        self.addStep(SetBuildProperty(
            name='set_hashType',
            property_name='hashType',
            value=self.hashType,
        ))
        # We need the basename of the current working dir so we can
        # ignore that dir when purging builds later.
        self.addStep(SetProperty(
            name='set_builddir',
            command=['bash', '-c', 'basename "$PWD"'],
            property='builddir',
            workdir='.',
        ))
        self.addStep(ShellCommand(
                     name='rm_buildtools',
                     command=['rm', '-rf', 'tools'],
                     description=['clobber', 'build tools'],
                     haltOnFailure=True,
                     log_eval_func=rc_eval_func({0: SUCCESS, None: RETRY}),
                     workdir='.'
                     ))
        self.addStep(MercurialCloneCommand(
                     name='clone_buildtools',
                     command=['hg', 'clone', self.buildToolsRepo, 'tools'],
                     description=['clone', 'build tools'],
                     log_eval_func=rc_eval_func({0: SUCCESS, None: RETRY}),
                     workdir='.',
                     retry=False  # We cannot use retry.py until we have this repo checked out
                     ))
        self.addStep(SetProperty(
            name='set_toolsdir',
            command=['bash', '-c', 'pwd'],
            property='toolsdir',
            workdir='tools',
        ))
        # Set properties for metadata about this slave
        self.addStep(SetProperty(
            name='set_instance_metadata',
            extract_fn=extractJSONProperties,
            command=['python', 'tools/buildfarm/maintenance/get_instance_metadata.py'],
            workdir='.',
            haltOnFailure=False,
            warnOnFailure=False,
            flunkOnFailure=False,
        ))

        if self.clobberURL is not None:
            self.addStep(MozillaClobberer(
                         name='checking_clobber_times',
                         branch=self.clobberBranch,
                         clobber_url=self.clobberURL,
                         clobberer_path=WithProperties(
                             '%(builddir)s/tools/clobberer/clobberer.py'),
                         clobberTime=self.clobberTime
                         ))

        if self.buildSpace > 0:
            command = ['python', 'tools/buildfarm/maintenance/purge_builds.py',
                       '-s', str(self.buildSpace)]

            for i in self.ignore_dirs:
                command.extend(["-n", i])

            # These are the base_dirs that get passed to purge_builds.py.
            # The mock dir is only present on linux slaves, but since
            # not all classes that inherit from MozillaBuildFactory provide
            # a platform property we can use for limiting the base_dirs, it
            # is easier to include mock by default and simply have
            # purge_builds.py skip the dir when it isn't present.
            command.extend(["..", "/mock/users/cltbld/home/cltbld/build"])

            def parse_purge_builds(rc, stdout, stderr):
                properties = {}
                for stream in (stdout, stderr):
                    m = re.search('unable to free (?P<size>[.\d]+) (?P<unit>\w+) ', stream, re.M)
                    if m:
                        properties['purge_target'] = '%s%s' % (
                            m.group('size'), m.group('unit'))
                    m = None
                    m = re.search('space only (?P<size>[.\d]+) (?P<unit>\w+)',
                                  stream, re.M)
                    if m:
                        properties['purge_actual'] = '%s%s' % (
                            m.group('size'), m.group('unit'))
                    m = None
                    m = re.search('(?P<size>[.\d]+) (?P<unit>\w+) of space available', stream, re.M)
                    if m:
                        properties['purge_actual'] = '%s%s' % (
                            m.group('size'), m.group('unit'))
                if 'purge_target' not in properties:
                    properties['purge_target'] = '%sGB' % str(self.buildSpace)
                return properties

            self.addStep(SetProperty(
                         name='clean_old_builds',
                         command=command,
                         description=['cleaning', 'old', 'builds'],
                         descriptionDone=['clean', 'old', 'builds'],
                         haltOnFailure=True,
                         workdir='.',
                         timeout=3600,  # One hour, because Windows is slow
                         extract_fn=parse_purge_builds,
                         log_eval_func=lambda c, s: regex_log_evaluator(
                             c, s, purge_error),
                         env=self.env,
                         ))

        if self.use_mock:
            self.addStep(MockReset(
                target=self.mock_target,
            ))
            self.addStep(MockInit(
                target=self.mock_target,
            ))
            self.addMockSteps()

    def addPeriodicRebootSteps(self):
        def do_disconnect(cmd):
            try:
                if 'SCHEDULED REBOOT' in cmd.logs['stdio'].getText():
                    return True
            except:
                pass
            return False
        self.addStep(DisconnectStep(
                     name='maybe_rebooting',
                     command=[
                         'python', 'tools/buildfarm/maintenance/count_and_reboot.py',
                         '-f', '../reboot_count.txt',
                         '-n', str(self.buildsBeforeReboot),
                         '-z'],
                     description=['maybe rebooting'],
                     force_disconnect=do_disconnect,
                     warnOnFailure=False,
                     flunkOnFailure=False,
                     alwaysRun=True,
                     workdir='.'
                     ))

    def getRepoName(self, repo):
        return repo.rstrip('/').split('/')[-1]

    def getRepository(self, repoPath, hgHost=None, push=False):
        assert repoPath
        for prefix in ('http://', 'ssh://', 'https://'):
            if repoPath.startswith(prefix):
                return repoPath
        if repoPath.startswith('/'):
            repoPath = repoPath.lstrip('/')
        if not hgHost:
            hgHost = self.hgHost
        proto = 'ssh' if push else 'https'
        return '%s://%s/%s' % (proto, hgHost, repoPath)

    def getPackageFilename(self, platform, platform_variation):
        if 'android-armv6' in self.complete_platform:
            packageFilename = '*arm-armv6.apk'
        elif 'android-x86' in self.complete_platform:
            packageFilename = '*android-i386.apk'
        elif 'android' in self.complete_platform:
            # the arm.apk is to avoid unsigned/unaligned apks
            packageFilename = '*arm.apk'
        elif 'maemo' in self.complete_platform:
            packageFilename = '*.linux-*-arm.tar.*'
        elif platform.startswith("linux64"):
            packageFilename = '*.linux-x86_64*.tar.bz2'
        elif platform.startswith("linux"):
            packageFilename = '*.linux-i686*.tar.bz2'
        elif platform.startswith("macosx"):
            packageFilename = '*.dmg'
        elif platform.startswith("win32"):
            packageFilename = '*.win32.zip'
        elif platform.startswith("win64"):
            packageFilename = '*.win64*.zip'
        else:
            return False
        return packageFilename

    def getInstallerFilename(self):
        return '*.installer.exe'

    def parseFileSize(self, propertyName):
        def getSize(rv, stdout, stderr):
            stdout = stdout.strip()
            return {propertyName: stdout.split()[4]}
        return getSize

    def parseFileHash(self, propertyName):
        def getHash(rv, stdout, stderr):
            stdout = stdout.strip()
            return {propertyName: stdout.split(' ', 2)[1]}
        return getHash

    def unsetFilepath(self, rv, stdout, stderr):
        return {'filepath': None}

    def addFilePropertiesSteps(self, filename, directory, fileType,
                               doStepIf=True, maxDepth=1, haltOnFailure=False):
        self.addStep(FindFile(
            name='find_filepath',
            description=['find', 'filepath'],
            doStepIf=doStepIf,
            filename=filename,
            directory=directory,
            filetype='file',
            max_depth=maxDepth,
            property_name='filepath',
            workdir='.',
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty(
            description=['set', fileType.lower(), 'filename'],
            doStepIf=doStepIf,
            command=['basename', WithProperties('%(filepath)s')],
            property=fileType + 'Filename',
            workdir='.',
            name='set_' + fileType.lower() + '_filename',
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty(
            description=['set', fileType.lower(), 'size', ],
            doStepIf=doStepIf,
            command=['bash', '-c',
                     WithProperties("ls -l %(filepath)s")],
            workdir='.',
            name='set_' + fileType.lower() + '_size',
            extract_fn=self.parseFileSize(propertyName=fileType + 'Size'),
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty(
            description=['set', fileType.lower(), 'hash', ],
            doStepIf=doStepIf,
            command=['bash', '-c',
                     WithProperties('openssl ' + 'dgst -' + self.hashType +
                                    ' %(filepath)s')],
            workdir='.',
            name='set_' + fileType.lower() + '_hash',
            extract_fn=self.parseFileHash(propertyName=fileType + 'Hash'),
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty(
            description=['unset', 'filepath', ],
            doStepIf=doStepIf,
            name='unset_filepath',
            command='echo "filepath:"',
            workdir=directory,
            extract_fn=self.unsetFilepath,
        ))

    def makeHgtoolStep(self, name='hg_update', repo_url=None, wc=None,
                       mirrors=None, bundles=None, env=None,
                       clone_by_revision=False, rev=None, workdir='build',
                       use_properties=True, locks=None, autoPurge=False):

        if not env:
            env = self.env

        if use_properties:
            env = env.copy()
            env['PROPERTIES_FILE'] = 'buildprops.json'

        cmd = ['python', WithProperties(
            "%(toolsdir)s/buildfarm/utils/hgtool.py")]

        if clone_by_revision:
            cmd.append('--clone-by-revision')

        if mirrors is None and self.baseMirrorUrls:
            mirrors = ["%s/%s" % (url, self.repoPath)
                       for url in self.baseMirrorUrls]

        if mirrors:
            for mirror in mirrors:
                cmd.extend(["--mirror", mirror])

        if bundles is None and self.baseBundleUrls:
            bundles = ["%s/%s.hg" % (url, self.getRepoName(
                self.repository)) for url in self.baseBundleUrls]

        if bundles:
            for bundle in bundles:
                cmd.extend(["--bundle", bundle])

        if not repo_url:
            repo_url = self.repository

        if rev:
            cmd.extend(["--rev", rev])

        cmd.append(repo_url)

        if wc:
            cmd.append(wc)

        if locks is None:
            locks = []

        if autoPurge:
            cmd.append('--purge')

        return RetryingShellCommand(
            name=name,
            command=cmd,
            timeout=60 * 60,
            env=env,
            workdir=workdir,
            haltOnFailure=True,
            flunkOnFailure=True,
            locks=locks,
        )

    def addGetTokenSteps(self):
        token = "token"
        nonce = "nonce"
        self.addStep(ShellCommand(
            command=['rm', '-f', nonce],
            workdir='.',
            name='rm_nonce',
            description=['remove', 'old', 'nonce'],
        ))
        self.addStep(SigningServerAuthenication(
            servers=self.signingServers,
            server_cert=SIGNING_SERVER_CERT,
            slavedest=token,
            workdir='.',
            name='download_token',
        ))


class MercurialBuildFactory(MozillaBuildFactory, MockMixin, TooltoolMixin):
    def __init__(self, objdir, platform, configRepoPath,
                 profiledBuild, mozconfig, srcMozconfig=None,
                 productName=None,
                 buildRevision=None, stageServer=None, stageUsername=None,
                 stageGroup=None, stageSshKey=None, stageBasePath=None,
                 stageProduct=None, post_upload_include_platform=False,
                 updatePlatform=None,
                 downloadBaseURL=None, nightly=False,
                 checkTest=False, valgrindCheck=False,
                 graphServer=None, graphSelector=None, graphBranch=None,
                 baseName=None, uploadPackages=True, uploadSymbols=True,
                 updates_enabled=False, createPartial=False, doCleanup=True,
                 packageSDK=False, packageTests=False, mozillaDir=None,
                 mozillaSrcDir=None,
                 enable_ccache=False, stageLogBaseUrl=None,
                 triggeredSchedulers=None, triggerBuilds=False,
                 useSharedCheckouts=False,
                 stagePlatform=None, testPrettyNames=False, l10nCheckTest=False,
                 disableSymbols=False,
                 doBuildAnalysis=False,
                 multiLocale=False,
                 multiLocaleMerge=True,
                 compareLocalesRepoPath=None,
                 compareLocalesTag='RELEASE_AUTOMATION',
                 mozharnessRepoPath=None,
                 mozharnessTag='default',
                 mozharness_repo_cache=None,
                 tools_repo_cache=None,
                 multiLocaleScript=None,
                 multiLocaleConfig=None,
                 mozharnessMultiOptions=None,
                 tooltool_manifest_src=None,
                 tooltool_bootstrap="setup.sh",
                 tooltool_url_list=None,
                 tooltool_script=None,
                 enablePackaging=True,
                 enableInstaller=False,
                 gaiaRepo=None,
                 gaiaRevision=None,
                 gaiaRevisionFile=None,
                 gaiaLanguagesFile=None,
                 gaiaLanguagesScript=None,
                 gaiaL10nRoot=None,
                 geckoL10nRoot=None,
                 geckoLanguagesFile=None,
                 relengapi_archiver_repo_path=None,
                 relengapi_archiver_release_tag=None,
                 **kwargs):
        MozillaBuildFactory.__init__(self, **kwargs)

        # Make sure we have a buildid and builduid
        self.addStep(FunctionalStep(
                     name='set_buildids',
                     func=setBuildIDProps,
                     ))

        self.objdir = objdir
        self.platform = platform
        self.configRepoPath = configRepoPath
        self.profiledBuild = profiledBuild
        self.mozconfig = mozconfig
        self.srcMozconfig = srcMozconfig
        self.productName = productName
        self.buildRevision = buildRevision
        self.stageServer = stageServer
        self.stageUsername = stageUsername
        self.stageGroup = stageGroup
        self.stageSshKey = stageSshKey
        self.stageBasePath = stageBasePath
        if stageBasePath and not stageProduct:
            self.stageProduct = productName
        else:
            self.stageProduct = stageProduct
        self.doBuildAnalysis = doBuildAnalysis
        self.updatePlatform = updatePlatform
        self.downloadBaseURL = downloadBaseURL
        self.nightly = nightly
        self.checkTest = checkTest
        self.valgrindCheck = valgrindCheck
        self.graphServer = graphServer
        self.graphSelector = graphSelector
        self.graphBranch = graphBranch
        self.baseName = baseName
        self.uploadPackages = uploadPackages
        self.uploadSymbols = uploadSymbols
        self.disableSymbols = disableSymbols
        self.updates_enabled = updates_enabled
        self.createPartial = createPartial
        self.doCleanup = doCleanup
        self.packageSDK = packageSDK
        self.enablePackaging = enablePackaging
        self.enableInstaller = enableInstaller
        self.packageTests = packageTests
        self.enable_ccache = enable_ccache
        self.triggeredSchedulers = triggeredSchedulers
        self.triggerBuilds = triggerBuilds
        self.post_upload_include_platform = post_upload_include_platform
        self.useSharedCheckouts = useSharedCheckouts
        self.testPrettyNames = testPrettyNames
        self.l10nCheckTest = l10nCheckTest
        self.tooltool_manifest_src = tooltool_manifest_src
        self.tooltool_url_list = tooltool_url_list or []
        self.tooltool_script = tooltool_script or ['/tools/tooltool.py']
        self.tooltool_bootstrap = tooltool_bootstrap
        self.gaiaRepo = gaiaRepo
        self.gaiaRepoUrl = "https://%s/%s" % (self.hgHost, self.gaiaRepo)
        self.gaiaMirrors = None
        if self.baseMirrorUrls and self.gaiaRepo:
            self.gaiaMirrors = ['%s/%s' % (url, self.gaiaRepo)
                                for url in self.baseMirrorUrls]
        self.gaiaRevision = gaiaRevision
        self.gaiaRevisionFile = gaiaRevisionFile
        self.geckoL10nRoot = geckoL10nRoot
        self.geckoLanguagesFile = geckoLanguagesFile
        self.gaiaLanguagesFile = gaiaLanguagesFile
        self.gaiaLanguagesScript = gaiaLanguagesScript
        self.gaiaL10nRoot = gaiaL10nRoot
        self.gaiaL10nBaseDir = WithProperties('%(basedir)s/build-gaia-l10n')
        self.compareLocalesRepoPath = compareLocalesRepoPath
        self.compareLocalesTag = compareLocalesTag
        self.multiLocaleScript = multiLocaleScript
        self.multiLocaleConfig = multiLocaleConfig
        self.multiLocaleMerge = multiLocaleMerge
        self.tools_repo_cache = tools_repo_cache
        self.relengapi_archiver_repo_path = relengapi_archiver_repo_path
        self.relengapi_archiver_release_tag = relengapi_archiver_release_tag

        assert len(self.tooltool_url_list) <= 1, "multiple urls not currently supported by tooltool"

        if self.uploadPackages:
            assert productName and stageServer and stageUsername
            assert stageBasePath

        self.complete_platform = self.platform
        # we don't need the extra cruft in 'platform' anymore
        self.platform = platform.split('-')[0]
        self.stagePlatform = stagePlatform
        # it turns out that the cruft is useful for dealing with multiple types
        # of builds that are all done using the same self.platform.
        # Examples of what happens:
        #   platform = 'linux' sets self.platform_variation to []
        #   platform = 'linux-opt' sets self.platform_variation to ['opt']
        platform_chunks = self.complete_platform.split('-', 1)
        if len(platform_chunks) > 1:
            self.platform_variation = platform_chunks[1].split('-')
        else:
            self.platform_variation = []

        assert self.platform in getSupportedPlatforms(
        ), "%s not in %s" % (self.platform, getSupportedPlatforms())

        if self.graphServer is not None:
            self.tbPrint = False
        else:
            self.tbPrint = True

        # SeaMonkey/Thunderbird make use of mozillaDir. Firefox does not.
        if mozillaDir:
            self.mozillaDir = '/%s' % (mozillaDir)
            self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)
            self.mozillaSrcDir = '%s' % self.mozillaDir
        else:
            self.mozillaDir = ''
            self.mozillaObjdir = self.objdir

            # Thunderbird now doesn't have mozillaDir, but still has a
            # mozillaSrcDir
            if mozillaSrcDir:
                self.mozillaSrcDir = '/%s' % (mozillaSrcDir)
            else:
                self.mozillaSrcDir = ''

        # These following variables are useful for sharing build steps (e.g.
        # update generation) with subclasses that don't use object dirs (e.g.
        # l10n repacks).
        #
        # We also concatenate the baseWorkDir at the outset to avoid having to
        # do that everywhere.
        self.absMozillaSrcDir = '%s%s' % (self.baseWorkDir, self.mozillaSrcDir)
        self.absMozillaObjDir = '%s/%s' % (
            self.baseWorkDir, self.mozillaObjdir)

        self.latestDir = '/pub/mozilla.org/%s' % self.stageProduct + \
                         '/nightly/latest-%s' % self.branchName
        if self.post_upload_include_platform:
            self.latestDir += '-%s' % self.stagePlatform

        self.stageLogBaseUrl = stageLogBaseUrl
        if self.stageLogBaseUrl:
            # yes, the branchName is needed twice here so that log uploads work
            # for all
            self.logUploadDir = '%s/%s-%s/' % (self.branchName, self.branchName,
                                               self.stagePlatform)
            self.logBaseUrl = '%s/%s' % (
                self.stageLogBaseUrl, self.logUploadDir)
        else:
            self.logUploadDir = 'tinderbox-builds/%s-%s/' % (self.branchName,
                                                             self.stagePlatform)
            self.logBaseUrl = 'http://%s/pub/mozilla.org/%s/%s' % \
                (self.stageServer,
                 self.stageProduct, self.logUploadDir)

        # Need to override toolsdir as set by MozillaBuildFactory because
        # we need Windows-style paths.
        if self.platform.startswith('win'):
            self.addStep(SetProperty(
                command=['bash', '-c', 'pwd -W'],
                property='toolsdir',
                workdir='tools'
            ))
            self.addStep(SetProperty(
                command=['bash', '-c', 'pwd -W'],
                property='basedir',
                workdir='.'
            ))
        if self.use_mock:
            self.addMockSteps()

        if self.enable_ccache:
            self.addStep(ShellCommand(command=['ccache', '-z'],
                                      name="clear_ccache_stats", warnOnFailure=False,
                                      flunkOnFailure=False, haltOnFailure=False, env=self.env))
        if mozharnessRepoPath:
            assert mozharnessRepoPath and mozharnessTag
            self.mozharnessRepoPath = mozharnessRepoPath
            self.mozharnessTag = mozharnessTag
            self.mozharness_repo_cache = mozharness_repo_cache

            self.mozharness_path = 'mozharness'  # relative to our work dir
            if self.mozharness_repo_cache:
                # in this case, we need to give it an absolute path as it
                # won't be in our work dir
                self.mozharness_path = self.mozharness_repo_cache
            self.addMozharnessRepoSteps()
        if multiLocale:
            assert compareLocalesRepoPath and compareLocalesTag
            assert mozharnessRepoPath and mozharnessTag
            assert multiLocaleScript and multiLocaleConfig
            if mozharnessMultiOptions:
                self.mozharnessMultiOptions = mozharnessMultiOptions
            else:
                self.mozharnessMultiOptions = ['--pull-locale-source',
                                               '--add-locales',
                                               '--package-multi',
                                               '--summary', ]

            self.addMultiLocaleRepoSteps()

        self.multiLocale = multiLocale

        if gaiaLanguagesFile:
            assert gaiaLanguagesScript and gaiaL10nRoot
            self.env['LOCALE_BASEDIR'] = self.gaiaL10nBaseDir
            self.env['LOCALES_FILE'] = self.gaiaLanguagesFile

        self.addBuildSteps()
        if self.uploadSymbols or (not self.disableSymbols and self.packageTests):
            self.addBuildSymbolsStep()
        if self.uploadSymbols:
            self.addUploadSymbolsStep()
        if self.enablePackaging or self.uploadPackages:
            self.addPackageSteps()
        if self.uploadPackages:
            self.addUploadSteps()
        if self.testPrettyNames:
            self.addTestPrettyNamesSteps()
        if self.l10nCheckTest:
            self.addL10nCheckTestSteps()
        if self.checkTest:
            self.addCheckTestSteps()
        if self.valgrindCheck:
            self.addValgrindCheckSteps()
        if self.updates_enabled and self.balrog_api_root:
            self.submitBalrogUpdates()
        if self.triggerBuilds:
            self.addTriggeredBuildsSteps()
        if self.doCleanup:
            self.addPostBuildCleanupSteps()
        if self.enable_ccache:
            self.addStep(ShellCommand(command=['ccache', '-s'],
                                      name="print_ccache_stats", warnOnFailure=False,
                                      flunkOnFailure=False, haltOnFailure=False, env=self.env))
        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def addMozharnessRepoSteps(self):
        # e.g. thunderbird releases define relengapi_archiver_repo_path
        # otherwise fall back on repoPath which is the Firefox Gecko repo
        archiver_repo = "--repo %s" % (self.relengapi_archiver_repo_path or self.repoPath,)
        if self.relengapi_archiver_release_tag:
            archiver_revision = "--tag %s" % self.relengapi_archiver_release_tag
        else:
            archiver_revision = "--rev %(revision)s"
        if self.mozharness_repo_cache:
            assert self.tools_repo_cache
            archiver_client_path = \
                os.path.join(self.tools_repo_cache,
                             'buildfarm',
                             'utils',
                             'archiver_client.py')
        else:
            self.addStep(ShellCommand(
                command=['bash', '-c',
                         'wget -Oarchiver_client.py ' +
                         '--no-check-certificate --tries=10 --waitretry=3 ' +
                         'http://hg.mozilla.org/build/tools/raw-file/default/buildfarm/utils/archiver_client.py'],
                haltOnFailure=True,
                workdir='.',
            ))
            archiver_client_path = 'archiver_client.py'


        self.addStep(ShellCommand(
            name='rm_mozharness',
            command=['rm', '-rf', 'mozharness'],
            description=['removing', 'mozharness'],
            descriptionDone=['remove', 'mozharness'],
            haltOnFailure=True,
            workdir='.',
        ))
        self.addStep(ShellCommand(
            name="download_and_extract_mozharness_archive",
            command=[
                'bash', '-c',
                WithProperties('python %s mozharness %s %s --debug' % (archiver_client_path,
                                                                       archiver_repo,
                                                                       archiver_revision))
            ],
            log_eval_func=rc_eval_func({0: SUCCESS, None: EXCEPTION}),
            workdir='.',
            haltOnFailure=True,
        ))

    def addMultiLocaleRepoSteps(self):
        name = self.compareLocalesRepoPath.rstrip('/').split('/')[-1]
        self.addStep(ShellCommand(
            name='rm_%s' % name,
            command=['rm', '-rf', '%s' % name],
            description=['removing', name],
            descriptionDone=['remove', name],
            haltOnFailure=True,
            workdir='.',
        ))
        self.addStep(MercurialCloneCommand(
            name='hg_clone_%s' % name,
            command=['hg', 'clone', self.getRepository(
                self.compareLocalesRepoPath), name],
            description=['checking', 'out', name],
            descriptionDone=['checkout', name],
            haltOnFailure=True,
            workdir='.',
        ))
        self.addStep(ShellCommand(
            name='hg_update_%s' % name,
            command=['hg', 'update', '-r', self.compareLocalesTag],
            description=['updating', name, 'to', self.compareLocalesTag],
            workdir=name,
            haltOnFailure=True
        ))

    def addTriggeredBuildsSteps(self,
                                triggeredSchedulers=None):
        '''Trigger other schedulers.
        We don't include these steps by default because different
        children may want to trigger builds at different stages.

        If triggeredSchedulers is None, then the schedulers listed in
        self.triggeredSchedulers will be triggered.
        '''
        if triggeredSchedulers is None:
            if self.triggeredSchedulers is None:
                return True
            triggeredSchedulers = self.triggeredSchedulers

        for triggeredScheduler in triggeredSchedulers:
            self.addStep(Trigger(
                schedulerNames=[triggeredScheduler],
                copy_properties=['buildid', 'builduid'],
                waitForFinish=False))

    def addBuildSteps(self):
        self.addPreBuildSteps()
        self.addSourceSteps()
        self.addConfigSteps()
        if self.signingServers and self.enableSigning:
            self.addGetTokenSteps()
        self.addDoBuildSteps()
        if self.doBuildAnalysis:
            self.addBuildAnalysisSteps()

    def addPreBuildSteps(self):
        if self.nightly:
            self.addStep(ShellCommand(
                         name='rm_builddir',
                         command=['rm', '-rf', 'build'],
                         env=self.env,
                         workdir='.',
                         timeout=60 * 60  # 1 hour
                         ))
        pkg_patterns = []
        for product in ('firefox-', 'fennec', 'seamonkey', 'thunderbird'):
            pkg_patterns.append('%s/dist/%s*' % (self.mozillaObjdir,
                                                 product))

        self.addStep(ShellCommand(
                     name='rm_old_pkg',
                     command="rm -rf %s %s/dist/install/sea/*.exe " %
                     (' '.join(pkg_patterns), self.mozillaObjdir),
                     env=self.env,
                     description=['deleting', 'old', 'package'],
                     descriptionDone=['delete', 'old', 'package']
                     ))

    def addGaiaSourceSteps(self):
        if self.gaiaRevisionFile:
            def parse_gaia_revision(rc, stdout, stderr):
                properties = {}
                stream = '\n'.join([stdout, stderr])
                # ugh, regex searching json output, since json.loads(stdout)
                # stack dumped on me
                m = re.search('"repo_path": "([^"]+)"', stream, re.M)
                if m:
                    properties['gaiaRepoPath'] = m.group(1)
                m = re.search('"revision": "(\w+)"', stream, re.M)
                if m:
                    properties['gaiaRevision'] = m.group(1)
                return properties
            self.addStep(SetProperty(
                command=['cat', self.gaiaRevisionFile],
                name="read_gaia_json",
                extract_fn=parse_gaia_revision,
            ))
            self.gaiaRevision = WithProperties("%(gaiaRevision)s")
            self.gaiaRepoUrl = WithProperties("https://" + self.hgHost + "/%(gaiaRepoPath)s")
            if self.baseMirrorUrls:
                self.gaiaMirrors = [WithProperties(url + "/%(gaiaRepoPath)s") for url in self.baseMirrorUrls]

        self.addStep(self.makeHgtoolStep(
            name="gaia_sources",
            rev=self.gaiaRevision or 'default',
            repo_url=self.gaiaRepoUrl,
            workdir="build/",
            use_properties=False,
            mirrors=self.gaiaMirrors,
            bundles=[],
            wc='gaia',
        ))
        if self.gaiaLanguagesFile:
            languagesFile = '%(basedir)s/build/gaia/' + \
                self.gaiaLanguagesFile
            cmd = ['python', '%s/%s' % (self.mozharness_path,
                                        self.gaiaLanguagesScript),
                   '--pull',
                   '--gaia-languages-file', WithProperties(languagesFile),
                   '--gaia-l10n-root', self.gaiaL10nRoot,
                   '--gaia-l10n-base-dir', self.gaiaL10nBaseDir,
                   '--config-file', self.multiLocaleConfig,
                   '--gecko-l10n-root', self.geckoL10nRoot]
            if self.geckoLanguagesFile:
                cmd.extend(
                    ['--gecko-languages-file', self.geckoLanguagesFile])
            self.addStep(MockCommand(
                name='clone_gaia_l10n_repos',
                command=cmd,
                env=self.env,
                workdir=WithProperties('%(basedir)s'),
                haltOnFailure=True,
                mock=self.use_mock,
                target=self.mock_target,
                mock_workdir_prefix=None,
            ))

    def addSourceSteps(self):
        if self.useSharedCheckouts:
            self.addStep(JSONPropertiesDownload(
                name="download_props",
                slavedest="buildprops.json",
                workdir='.'
            ))

            self.addStep(self.makeHgtoolStep(wc='build', workdir='.'))

            self.addStep(SetProperty(
                name='set_got_revision',
                command=['hg', 'parent', '--template={node}'],
                extract_fn=short_hash
            ))
        else:
            self.addStep(Mercurial(
                         name='hg_update',
                         mode='update',
                         baseURL='https://%s/' % self.hgHost,
                         defaultBranch=self.repoPath,
                         timeout=60 * 60,  # 1 hour
                         ))

        if self.buildRevision:
            self.addStep(ShellCommand(
                         name='hg_update',
                         command=['hg', 'up', '-C', '-r', self.buildRevision],
                         haltOnFailure=True
                         ))
            self.addStep(SetProperty(
                         name='set_got_revision',
                         command=['hg', 'identify', '-i'],
                         property='got_revision'
                         ))

        if self.gaiaRepo:
            self.addGaiaSourceSteps()
        self.addStep(SetBuildProperty(
            name='set_comments',
            property_name="comments",
            value=lambda build: build.source.changes[-
                                                     1].comments if len(
                                                         build.source.changes) > 0 else "",
        ))

    def addConfigSteps(self):
        self.addStep(ShellCommand(
            name='get_mozconfig',
            command=['cp', self.srcMozconfig, '.mozconfig'],
            description=['getting', 'mozconfig'],
            descriptionDone=['got', 'mozconfig'],
            haltOnFailure=True
        ))
        self.addStep(ShellCommand(
                     name='cat_mozconfig',
                     command=['cat', '.mozconfig'],
                     ))
        if self.tooltool_manifest_src:
            self.addTooltoolStep(workdir='build')

    def addDoBuildSteps(self):
        workdir = WithProperties('%(basedir)s/build')
        if self.platform.startswith('win'):
            workdir = "build"
        command = self.makeCmd + ['-f', 'client.mk', 'build',
                                  WithProperties('MOZ_BUILD_DATE=%(buildid:-)s')]

        if self.profiledBuild:
            command.append('MOZ_PGO=1')
        compile_Env = self.env.copy()
        compile_Env.update({'TOOLTOOL_DIR': WithProperties('%(basedir)s/build')})
        self.addStep(MockCommand(
                     name='compile',
                     command=command,
                     description=['compile'],
                     env=compile_Env,
                     haltOnFailure=True,
                     # bug 1027983
                     timeout=3 * 3600,  # max of 3 hours without output before killing
                     maxTime=int(5.5 * 3600),  # max of 5.5 hours total runtime before killing
                     mock=self.use_mock,
                     target=self.mock_target,
                     workdir=workdir,
                     mock_workdir_prefix=None,
                     ))

    def addBuildInfoSteps(self):
        """Helper function for getting build information into properties.
        Looks for self._gotBuildInfo to make sure we only run this set of steps
        once."""
        if not getattr(self, '_gotBuildInfo', False):
            printconfig_env = self.env.copy()
            printconfig_env.update({'TOOLTOOL_DIR': WithProperties('%(basedir)s/build')})
            del printconfig_env['MOZ_OBJDIR']
            printconfig_workdir = WithProperties('%(basedir)s/build/' + self.objdir)
            # hax https://bugzilla.mozilla.org/show_bug.cgi?id=1232466#c10
            if self.platform.startswith('win'):
                python = ['c:/mozilla-build/python27/python', '-u']
            else:
                python = ['/tools/buildbot/bin/python']
            if self.mozillaSrcDir:
                machPath = '%(basedir)s/build/mozilla/mach'
            else:
                machPath = '%(basedir)s/build/mach'
            # we need abs paths because we are in a non relative workdir
            printconfig_base_command = python + [
                WithProperties(machPath), 'python',
                WithProperties('%(basedir)s/build' + '%s/config/printconfigsetting.py' % self.mozillaSrcDir),
                WithProperties('%(basedir)s/build' + '/%s/dist/bin/application.ini' % self.mozillaObjdir),
            ]

            self.addStep(SetProperty(
                command=printconfig_base_command + ['App', 'BuildID'],
                property='buildid',
                workdir=printconfig_workdir,
                env=printconfig_env,
                description=['getting', 'buildid'],
                descriptionDone=['got', 'buildid'],
            ))
            self.addStep(SetProperty(
                command=printconfig_base_command + ['App', 'SourceStamp'],
                property='sourcestamp',
                workdir=printconfig_workdir,
                env=printconfig_env,
                description=['getting', 'sourcestamp'],
                descriptionDone=['got', 'sourcestamp']
            ))
            self._gotBuildInfo = True

    def addBuildAnalysisSteps(self):
        if self.platform in ('linux', 'linux64'):
            # Analyze the number of ctors
            def get_ctors(rc, stdout, stderr):
                try:
                    output = stdout.split("\t")
                    num_ctors = int(output[0])
                    testresults = [(
                        'num_ctors', 'num_ctors', num_ctors, str(num_ctors))]
                    return dict(num_ctors=num_ctors, testresults=testresults)
                except:
                    return {'testresults': []}

            self.addStep(SetProperty(
                name='get_ctors',
                command=['python', WithProperties('%(toolsdir)s/buildfarm/utils/count_ctors.py'),
                         '%s/dist/bin/libxul.so' % self.mozillaObjdir],
                extract_fn=get_ctors,
            ))

            if self.graphServer:
                self.addBuildInfoSteps()
                self.addStep(
                    JSONPropertiesDownload(slavedest="properties.json"))
                gs_env = self.env.copy()
                gs_env['PYTHONPATH'] = WithProperties(
                    '%(toolsdir)s/lib/python')
                self.addStep(GraphServerPost(server=self.graphServer,
                                             selector=self.graphSelector,
                                             branch=self.graphBranch,
                                             resultsname=self.baseName,
                                             env=gs_env,
                                             flunkOnFailure=False,
                                             haltOnFailure=False,
                                             propertiesFile="properties.json"))
            else:
                self.addStep(OutputStep(
                    name='tinderboxprint_ctors',
                    data=WithProperties(
                        'TinderboxPrint: num_ctors: %(num_ctors:-unknown)s'),
                ))

    def addCheckTestSteps(self):
        env = self.env.copy()
        env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        env['MINIDUMP_SAVE_PATH'] = WithProperties('%(basedir:-)s/minidumps')
        env.update({'TOOLTOOL_DIR': WithProperties('%(basedir)s/build')})
        self.addStep(MockMozillaCheck(
                     test_name="check",
                     makeCmd=self.makeCmd,
                     warnOnWarnings=True,
                     workdir=WithProperties(
                         '%(basedir)s/build/' + self.objdir),
                     timeout=10 * 60,  # 10 minutes.
                     env=env,
                     target=self.mock_target,
                     mock=self.use_mock,
                     mock_workdir_prefix=None,
                     ))

    def addL10nCheckTestSteps(self):
        # We override MOZ_SIGN_CMD here because it's not necessary
        # Disable signing for l10n check steps
        env = self.env
        if 'MOZ_SIGN_CMD' in env:
            env = env.copy()
            del env['MOZ_SIGN_CMD']

        self.addStep(MockCommand(
                     name='make l10n check',
                     command=self.makeCmd + ['l10n-check'],
                     workdir='build/%s' % self.objdir,
                     env=env,
                     haltOnFailure=False,
                     flunkOnFailure=False,
                     warnOnFailure=True,
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))

    def addValgrindCheckSteps(self):
        env = self.env.copy()
        env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        env['MINIDUMP_SAVE_PATH'] = WithProperties('%(basedir:-)s/minidumps')
        self.addStep(unittest_steps.MozillaCheck,
                     test_name="check-valgrind",
                     warnOnWarnings=True,
                     workdir="build/%s/js/src" % self.mozillaObjdir,
                     timeout=5 * 60,  # 5 minutes.
                     env=env,
                     )

    def addCreateUpdateSteps(self):
        self.addStep(MockCommand(
            name='make_complete_mar',
            command=self.makeCmd + ['-C',
                     '%s/tools/update-packaging' % self.mozillaObjdir],
            env=self.env,
            haltOnFailure=True,
            workdir='build',
            mock=self.use_mock,
            target=self.mock_target,
        ))
        self.addFilePropertiesSteps(
            filename='*.complete.mar',
            directory='%s/dist/update' % self.absMozillaObjDir,
            fileType='completeMar',
            haltOnFailure=True,
        )

    def addTestPrettyNamesSteps(self):
        # Disable signing for l10n check steps
        env = self.env.copy()
        if 'MOZ_SIGN_CMD' in env:
            env = env.copy()
            del env['MOZ_SIGN_CMD']

        if self.complete_platform == 'macosx64':
            # make package is broken in universal (opt) builds) if
            # MOZ_OBJDIR is set but you're calling in a specific arch, bug 1195546
            env.update({'MOZ_CURRENT_PROJECT': os.path.basename(self.objdir)})

        if 'mac' in self.platform:
            # Need to run this target or else the packaging targets will
            # fail.
            self.addStep(ShellCommand(
                         name='postflight_all',
                         command=self.makeCmd + [
                             '-f', 'client.mk', 'postflight_all'],
                         env=env,
                         haltOnFailure=False,
                         flunkOnFailure=False,
                         warnOnFailure=False,
                         ))
        pkg_targets = ['package', 'package-tests']
        if self.enableInstaller:
            pkg_targets.append('installer')
        makeEnv = env.copy()
        makeEnv.update({'TOOLTOOL_DIR': WithProperties('%(basedir)s/build')})
        for t in pkg_targets:
            self.addStep(MockCommand(
                         name='make %s pretty' % t,
                         command=self.makeCmd + [t, 'MOZ_PKG_PRETTYNAMES=1'],
                         env=env,
                         workdir='build/%s' % self.objdir,
                         haltOnFailure=False,
                         flunkOnFailure=False,
                         warnOnFailure=True,
                         mock=self.use_mock,
                         target=self.mock_target,
                         ))
        self.addStep(MockCommand(
                     name='make update pretty',
                     command=self.makeCmd + ['-C',
                                             '%s/tools/update-packaging' % self.mozillaObjdir,
                                             'MOZ_PKG_PRETTYNAMES=1'],
                     env=makeEnv,
                     haltOnFailure=False,
                     flunkOnFailure=False,
                     warnOnFailure=True,
                     workdir='build',
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        if self.l10nCheckTest:
            self.addStep(MockCommand(
                         name='make l10n check pretty',
                         command=self.makeCmd + [
                             'l10n-check', 'MOZ_PKG_PRETTYNAMES=1'],
                         workdir='build/%s' % self.objdir,
                         env=env,
                         haltOnFailure=False,
                         flunkOnFailure=False,
                         warnOnFailure=True,
                         mock=self.use_mock,
                         target=self.mock_target,
                         ))

    def addPackageSteps(self, pkgArgs=None, pkgTestArgs=None):
        pkgArgs = pkgArgs or []
        pkgTestArgs = pkgTestArgs or []

        if self.env:
            pkg_env = self.env.copy()
        else:
            pkg_env = {}

        objdir = WithProperties('%(basedir)s/build/' + self.objdir)
        if self.platform.startswith('win'):
            objdir = "build/%s" % self.objdir
        workdir = WithProperties('%(basedir)s/build')
        if self.platform.startswith('win'):
            workdir = "build/"

        pkg_env2 = pkg_env.copy()
        if self.complete_platform == 'macosx64':
            # make package is broken in universal (opt) builds) if
            # MOZ_OBJDIR is set but you're calling in a specific arch, bug 1195546
            pkg_env2.update({'MOZ_CURRENT_PROJECT': os.path.basename(self.objdir)})

        if self.packageSDK:
            self.addStep(MockCommand(
                         name='make_sdk',
                         command=self.makeCmd + ['-f', 'client.mk', 'sdk'],
                         env=pkg_env2,
                         workdir=workdir,
                         mock=self.use_mock,
                         target=self.mock_target,
                         haltOnFailure=True,
                         mock_workdir_prefix=None,
                         ))
        if self.packageTests:
            self.addStep(MockCommand(
                         name='make_pkg_tests',
                         command=self.makeCmd + [
                             'package-tests'] + pkgTestArgs,
                         env=pkg_env2,
                         workdir=objdir,
                         mock=self.use_mock,
                         target=self.mock_target,
                         mock_workdir_prefix=None,
                         haltOnFailure=True,
                         ))

        self.addStep(MockCommand(
            name='make_pkg',
            command=self.makeCmd + ['package'] + pkgArgs,
            env=pkg_env2,
            workdir=objdir,
            mock=self.use_mock,
            target=self.mock_target,
            mock_workdir_prefix=None,
            haltOnFailure=True,
        ))

        # Get package details
        self.packageFilename = self.getPackageFilename(self.platform,
                                                       self.platform_variation)
        if self.packageFilename and self.productName not in ('b2g',):
            self.addFilePropertiesSteps(filename=self.packageFilename,
                                        directory='build/%s/dist' % self.mozillaObjdir,
                                        fileType='package',
                                        haltOnFailure=True)
        # Windows special cases
        installerFilename = self.getInstallerFilename()
        if self.enableInstaller:
            self.addStep(ShellCommand(
                name='make_installer',
                command=self.makeCmd + ['installer'] + pkgArgs,
                env=pkg_env,
                workdir='build/%s' % self.objdir,
                haltOnFailure=True
            ))
            if installerFilename:
                self.addFilePropertiesSteps(filename=installerFilename,
                                            directory='build/%s/dist/install/sea' % self.mozillaObjdir,
                                            fileType='installer',
                                            haltOnFailure=True)

        printconfig_env = self.env.copy()
        printconfig_env.update({'TOOLTOOL_DIR': WithProperties('%(basedir)s/build')})
        del printconfig_env['MOZ_OBJDIR']
        printconfig_workdir = WithProperties('%(basedir)s/build/' + self.objdir)
        # hax https://bugzilla.mozilla.org/show_bug.cgi?id=1232466#c10
        if self.platform.startswith('win'):
            python = ['c:/mozilla-build/python27/python', '-u']
        else:
            python = ['/tools/buildbot/bin/python']
        if self.mozillaSrcDir:
            machPath = '%(basedir)s/build/mozilla/mach'
        else:
            machPath = '%(basedir)s/build/mach'
        # we need abs paths because we are in a non relative workdir
        printconfig_base_command = python + [
            WithProperties(machPath), 'python',
            WithProperties('%(basedir)s/build' + '%s/config/printconfigsetting.py' % self.mozillaSrcDir),
            WithProperties('%(basedir)s/build' + '/%s/dist/bin/application.ini' % self.mozillaObjdir),
        ]
        self.addStep(SetProperty(
            command=printconfig_base_command + ['App', 'BuildID'],
            property='buildid',
            workdir=printconfig_workdir,
            env=printconfig_env,
            name='get_build_id',
        ))
        self.addStep(SetProperty(
            command=printconfig_base_command + ['App', 'Version'],
            property='appVersion',
            workdir=printconfig_workdir,
            env=printconfig_env,
            name='get_app_version',
        ))
        self.addStep(SetProperty(
            command=printconfig_base_command + ['App', 'Name'],
            property='appName',
            workdir=printconfig_workdir,
            env=printconfig_env,
            name='get_app_name',
        ))
        self.pkg_env = pkg_env

    def addUploadSteps(self):
        if self.multiLocale:
            self.doUpload(postUploadBuildDir='en-US')
            cmd = ['python', '%s/%s' % (self.mozharness_path, self.multiLocaleScript),
                   '--config-file', self.multiLocaleConfig]
            if self.multiLocaleMerge:
                cmd.append('--merge-locales')
            if self.gaiaLanguagesFile:
                cmd.extend(['--gaia-languages-file', WithProperties(
                    '%(basedir)s/build/gaia/' + self.gaiaLanguagesFile)])
            if self.gaiaL10nRoot:
                cmd.extend(['--gaia-l10n-root', self.gaiaL10nRoot])
            if self.geckoLanguagesFile:
                cmd.extend(['--gecko-languages-file', self.geckoLanguagesFile])
            if self.geckoL10nRoot:
                cmd.extend(['--gecko-l10n-root', self.geckoL10nRoot])
            cmd.extend(self.mozharnessMultiOptions)
            self.addStep(MockCommand(
                name='mozharness_multilocale',
                command=cmd,
                env=self.pkg_env,
                workdir=WithProperties('%(basedir)s'),
                haltOnFailure=True,
                mock=self.use_mock,
                target=self.mock_target,
                mock_workdir_prefix=None,
            ))
            # b2g doesn't get snippets, and these steps don't work it, so don't
            # run them. Also ignore the Android release case where
            # packageFilename is undefined (bug 739959)
            if self.productName != 'b2g' and self.packageFilename:
                self.addFilePropertiesSteps(filename=self.packageFilename,
                                            directory='build/%s/dist' % self.mozillaObjdir,
                                            fileType='package',
                                            haltOnFailure=True)

        if self.updates_enabled and 'android' not in self.complete_platform:
            self.addCreateUpdateSteps()

        # Call out to a subclass to do the actual uploading
        self.doUpload(uploadMulti=self.multiLocale)

    def submitBalrogUpdates(self, type_='nightly'):
        if 'android' in self.complete_platform:
            self.addFilePropertiesSteps(
                filename=self.packageFilename,
                directory='build/%s/dist' % self.mozillaObjdir,
                fileType='completeMar',
                haltOnFailure=True)
            self.addStep(SetBuildProperty(
                name='set_completeMarUrl',
                property_name='completeMarUrl',
                value=lambda b: b.getProperty('packageUrl'),
            ))

        self.addStep(JSONPropertiesDownload(
            name='download_balrog_props',
            slavedest='buildprops_balrog.json',
            workdir='.',
            flunkOnFailure=False,
        ))
        cmd = [
            self.env.get('PYTHON26', 'python'),
            WithProperties(
                '%(toolsdir)s/scripts/updates/balrog-submitter.py'),
            '--build-properties', 'buildprops_balrog.json',
            '--api-root', self.balrog_api_root,
            '--username', self.balrog_username,
            '-t', type_, '--verbose',
        ]
        if self.balrog_submitter_extra_args:
            cmd.extend(self.balrog_submitter_extra_args)
        if self.balrog_credentials_file:
            credentialsFile = os.path.join(os.getcwd(),
                                           self.balrog_credentials_file)
            target_file_name = os.path.basename(credentialsFile)
            cmd.extend(['--credentials-file', target_file_name])
            self.addStep(FileDownload(
                mastersrc=credentialsFile,
                slavedest=target_file_name,
                workdir='.',
                flunkOnFailure=False,
            ))
        self.addStep(RetryingShellCommand(
            name='submit_balrog_updates',
            command=cmd,
            workdir='.',
            flunkOnFailure=True,
        ))

    def addBuildSymbolsStep(self):
        objdir = WithProperties('%(basedir)s/build/' + self.objdir)
        if self.platform.startswith('win'):
            objdir = 'build/%s' % self.objdir
        self.addStep(MockCommand(
                     name='make_buildsymbols',
                     command=self.makeCmd + ['buildsymbols'],
                     env=self.env,
                     workdir=objdir,
                     mock=self.use_mock,
                     target=self.mock_target,
                     mock_workdir_prefix=None,
                     haltOnFailure=True,
                     timeout=60 * 60,
                     ))

    def addUploadSymbolsStep(self):
        objdir = WithProperties('%(basedir)s/build/' + self.objdir)
        if self.platform.startswith('win'):
            objdir = 'build/%s' % self.objdir
        self.addStep(RetryingMockCommand(
                     name='make_uploadsymbols',
                     command=self.makeCmd + ['uploadsymbols'],
                     env=self.env,
                     workdir=objdir,
                     mock=self.use_mock,
                     target=self.mock_target,
                     mock_workdir_prefix=None,
                     haltOnFailure=True,
                     timeout=2400,  # 40 minutes
                     ))

    def addPostBuildCleanupSteps(self):
        if self.nightly:
            self.addStep(ShellCommand(
                         name='rm_builddir',
                         command=['rm', '-rf', 'build'],
                         env=self.env,
                         workdir='.',
                         timeout=5400  # 1.5 hours
                         ))


class TryBuildFactory(MercurialBuildFactory):
    def __init__(
        self, talosMasters=None, unittestMasters=None, packageUrl=None,
        packageDir=None, unittestBranch=None, tinderboxBuildsDir=None,
            **kwargs):

        self.packageUrl = packageUrl
        # The directory the packages go into
        self.packageDir = packageDir

        if talosMasters is None:
            self.talosMasters = []
        else:
            assert packageUrl
            self.talosMasters = talosMasters

        self.unittestMasters = unittestMasters or []
        self.unittestBranch = unittestBranch

        if self.unittestMasters:
            assert self.unittestBranch
            assert packageUrl

        self.tinderboxBuildsDir = tinderboxBuildsDir

        MercurialBuildFactory.__init__(self, **kwargs)

    def addSourceSteps(self):
        if self.useSharedCheckouts:
            # We normally rely on the Mercurial step to clobber for us, but
            # since we're managing the checkout ourselves...
            self.addStep(JSONPropertiesDownload(
                name="download_props",
                slavedest="buildprops.json",
                workdir='.'
            ))

            step = self.makeHgtoolStep(
                clone_by_revision=True,
                wc='build',
                workdir='.',
                autoPurge=True,
                locks=[hg_try_lock.access('counting')],
            )
            self.addStep(step)

        else:
            self.addStep(Mercurial(
                         name='hg_update',
                         mode='clobber',
                         baseURL='https://%s/' % self.hgHost,
                         defaultBranch=self.repoPath,
                         timeout=60 * 60,  # 1 hour
                         locks=[hg_try_lock.access('counting')],
                         ))

        if self.buildRevision:
            self.addStep(ShellCommand(
                         name='hg_update',
                         command=['hg', 'up', '-C', '-r', self.buildRevision],
                         haltOnFailure=True
                         ))
        self.addStep(SetProperty(
                     name='set_got_revision',
                     command=['hg', 'parent', '--template={node}'],
                     extract_fn=short_hash
                     ))
        if self.gaiaRepo:
            self.addGaiaSourceSteps()
        self.addStep(SetBuildProperty(
            name='set_comments',
            property_name="comments",
            value=lambda build: build.source.changes[-
                                                     1].comments if len(
                                                         build.source.changes) > 0 else "",
        ))

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        self.addStep(SetBuildProperty(
                     name='set_who',
                     property_name='who',
                     value=lambda build: str(build.source.changes[0].who) if len(
                         build.source.changes) > 0 else "nobody@example.com",
                     haltOnFailure=True
                     ))

        uploadEnv = self.env.copy()
        uploadEnv.update({
            'UPLOAD_HOST': self.stageServer,
            'UPLOAD_USER': self.stageUsername,
            'UPLOAD_TO_TEMP': '1',
        })

        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        # Set up the post upload to the custom try tinderboxBuildsDir
        tinderboxBuildsDir = self.packageDir

        uploadEnv['POST_UPLOAD_CMD'] = postUploadCmdPrefix(
            upload_dir=tinderboxBuildsDir,
            product=self.productName,
            revision=WithProperties('%(revision)s'),
            who=WithProperties('%(who)s'),
            builddir=WithProperties('%(branch)s-%(stage_platform)s'),
            buildid=WithProperties('%(buildid)s'),
            to_try=True,
            to_dated=False,
            as_list=False,
        )

        objdir = WithProperties('%(basedir)s/build/' + self.objdir)
        if self.platform.startswith('win'):
            objdir = 'build/%s' % self.objdir
        upload_vars = []
        if uploadMulti:
            upload_vars.append("AB_CD=multi")
        self.addStep(RetryingMockProperty(
                     command=self.makeCmd + ['upload'] + upload_vars,
                     env=uploadEnv,
                     workdir=objdir,
                     mock=self.use_mock,
                     target=self.mock_target,
                     extract_fn=parse_make_upload,
                     haltOnFailure=True,
                     description=["upload"],
                     timeout=40 * 60,  # 40 minutes
                     log_eval_func=lambda c, s: regex_log_evaluator(
                         c, s, upload_errors),
                     locks=[upload_lock.access('counting')],
                     mock_workdir_prefix=None,
                     ))

        talosBranch = "%s-%s-talos" % (self.branchName, self.complete_platform)
        sendchange_props = {
            'buildid': WithProperties('%(buildid:-)s'),
            'builduid': WithProperties('%(builduid:-)s'),
        }

        # We don't want to run sendchanges for the multilocale builds we just
        # uploaded, so we can return early now.
        if uploadMulti:
            return

        for master, warn, retries in self.talosMasters:
            self.addStep(SendChangeStep(
                         name='sendchange_%s' % master,
                         warnOnFailure=warn,
                         master=master,
                         retries=retries,
                         branch=talosBranch,
                         revision=WithProperties('%(got_revision)s'),
                         files=[WithProperties('%(packageUrl)s')],
                         user=WithProperties('%(who)s'),
                         comments=WithProperties('%(comments:-)s'),
                         sendchange_props=sendchange_props,
                         env=self.env,
                         ))
        if self.packageTests:
            for master, warn, retries in self.unittestMasters:
                self.addStep(SendChangeStep(
                             name='sendchange_%s' % master,
                             warnOnFailure=warn,
                             master=master,
                             retries=retries,
                             branch=self.unittestBranch,
                             revision=WithProperties('%(got_revision)s'),
                             files=[WithProperties('%(packageUrl)s'),
                                    WithProperties('%(testsUrl)s')],
                             user=WithProperties('%(who)s'),
                             comments=WithProperties('%(comments:-)s'),
                             sendchange_props=sendchange_props,
                             env=self.env,
                             ))


def marFilenameToProperty(prop_name=None):
    '''Parse a file listing and return the first mar filename found as
       a named property.
    '''
    def parseMarFilename(rc, stdout, stderr):
        if prop_name is not None:
            for line in filter(None, stdout.split('\n')):
                line = line.strip()
                if re.search(r'\.mar$', line):
                    return {prop_name: line}
        return {}
    return parseMarFilename


class NightlyBuildFactory(MercurialBuildFactory):
    def __init__(self, talosMasters=None, unittestMasters=None,
                 unittestBranch=None, tinderboxBuildsDir=None,
                 **kwargs):

        self.talosMasters = talosMasters or []

        self.unittestMasters = unittestMasters or []
        self.unittestBranch = unittestBranch

        if self.unittestMasters:
            assert self.unittestBranch

        self.tinderboxBuildsDir = tinderboxBuildsDir

        MercurialBuildFactory.__init__(self, **kwargs)

    def makePartialTools(self):
        '''The mar and bsdiff tools are created by default when
           --enable-update-packaging is specified, but some subclasses may
           need to explicitly build the tools.
        '''
        pass

    def downloadMarTools(self):
        '''The mar and bsdiff tools are created by default when
           --enable-update-packaging is specified. The latest version should
           always be available latest-${branch}/mar-tools/${platform}
           on the ftp server.
        '''
        pass

    def getCompleteMarPatternMatch(self):
        marPattern = getPlatformFtpDir(self.platform)
        if not marPattern:
            return False
        marPattern += '.complete.mar'
        return marPattern

    def previousMarExists(self, step):
        return "previousMarFilename" in step.build.getProperties() and len(step.build.getProperty("previousMarFilename")) > 0

    def addCreatePartialUpdateSteps(self, extraArgs=None):
        '''This function expects that the following build properties are
           already set: buildid, completeMarFilename
        '''
        # These tools (mar+mbsdiff) should now be built (or downloaded).
        mar = '../dist/host/bin/mar'
        mbsdiff = '../dist/host/bin/mbsdiff'
        # Unpack the current complete mar we just made.
        updateEnv = self.env.copy()
        updateEnv['MAR'] = mar
        updateEnv['MBSDIFF'] = mbsdiff
        self.addStep(ShellCommand(
            name='rm_unpack_dirs',
            command=['rm', '-rf', 'current', 'current.work', 'previous'],
            env=updateEnv,
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        self.addStep(ShellCommand(
            name='make_unpack_dirs',
            command=['mkdir', 'current', 'previous'],
            env=updateEnv,
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        self.addStep(MockCommand(
            name='unpack_current_mar',
            command=['perl',
                     WithProperties('%(basedir)s/' +
                                    self.absMozillaSrcDir +
                                    '/tools/update-packaging/unwrap_full_update.pl'),
                     WithProperties('../dist/update/%(completeMarFilename)s')],
            env=updateEnv,
            haltOnFailure=True,
            workdir='%s/current' % self.absMozillaObjDir,
            mock=self.use_mock,
            target=self.mock_target,
        ))
        # The mar file name will be the same from one day to the next,
        # *except* when we do a version bump for a release. To cope with
        # this, we get the name of the previous complete mar directly
        # from staging. Version bumps can also often involve multiple mars
        # living in the latest dir, so we grab the latest one.
        marPattern = self.getCompleteMarPatternMatch()
        self.addStep(SetProperty(
            name='get_previous_mar_filename',
            description=['get', 'previous', 'mar', 'filename'],
            command=['bash', '-c',
                     WithProperties(
                         'ssh -l %s -i ~/.ssh/%s %s ' % (self.stageUsername,
                                                         self.stageSshKey,
                                                         self.stageServer) +
                         'ls -1t %s | grep %s$ | head -n 1' % (self.latestDir,
                                                               marPattern))
                     ],
            extract_fn=marFilenameToProperty(prop_name='previousMarFilename'),
            flunkOnFailure=False,
            haltOnFailure=False,
            warnOnFailure=True
        ))
        previousMarURL = WithProperties('http://%s' % self.stageServer +
                                        '%s' % self.latestDir +
                                        '/%(previousMarFilename)s')
        self.addStep(RetryingMockCommand(
            name='get_previous_mar',
            description=['get', 'previous', 'mar'],
            doStepIf=self.previousMarExists,
            command=['wget', '-O', 'previous.mar', '--no-check-certificate',
                     previousMarURL],
            workdir='%s/dist/update' % self.absMozillaObjDir,
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
        ))
        # Unpack the previous complete mar.
        self.addStep(MockCommand(
            name='unpack_previous_mar',
            description=['unpack', 'previous', 'mar'],
            doStepIf=self.previousMarExists,
            command=['perl',
                     WithProperties('%(basedir)s/' +
                                    self.absMozillaSrcDir +
                                    '/tools/update-packaging/unwrap_full_update.pl'),
                     '../dist/update/previous.mar'],
            env=updateEnv,
            workdir='%s/previous' % self.absMozillaObjDir,
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
        ))
        # Extract the build ID from the unpacked previous complete mar.
        self.addStep(FindFile(
            name='find_inipath',
            description=['find', 'inipath'],
            doStepIf=self.previousMarExists,
            filename='application.ini',
            directory='previous',
            filetype='file',
            max_depth=4,
            property_name='previous_inipath',
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        printconfig_env = self.env.copy()
        printconfig_env.update({'TOOLTOOL_DIR': WithProperties('%(basedir)s/build')})
        del printconfig_env['MOZ_OBJDIR']
        printconfig_workdir = WithProperties('%(basedir)s/build/' + self.objdir)
        # hax https://bugzilla.mozilla.org/show_bug.cgi?id=1232466#c10
        if self.platform.startswith('win'):
            python = ['c:/mozilla-build/python27/python', '-u']
        else:
            python = ['/tools/buildbot/bin/python']
        if self.mozillaSrcDir:
            machPath = '%(basedir)s/build/mozilla/mach'
        else:
            machPath = '%(basedir)s/build/mach'
        # we need abs paths because we are in a non relative workdir
        printconfig_base_command = python + [
            WithProperties(machPath), 'python',
            # abs*Dir attrs lie. they are not absolute paths
            WithProperties('%(basedir)s/' + '%s/config/printconfigsetting.py' % self.absMozillaSrcDir),
            WithProperties('%(basedir)s/' + self.absMozillaObjDir + '/%(previous_inipath)s')
        ]
        self.addStep(SetProperty(
            name='set_previous_buildid',
            description=['set', 'previous', 'buildid'],
            doStepIf=self.previousMarExists,
            command=printconfig_base_command + ['App', 'BuildID'],
            property='previous_buildid',
            workdir=printconfig_workdir,
            env=printconfig_env,
            haltOnFailure=True,
        ))
        for dir in ['current', 'previous']:
            self.addStep(ShellCommand(
                name='remove pgc files (%s)' % dir,
                command="find . -name \*.pgc -print -delete",
                env=updateEnv,
                workdir="%s/%s" % (self.absMozillaObjDir, dir),
                flunkOnFailure=False,
                haltOnFailure=False,
            ))
        # Generate the partial patch from the two unpacked complete mars.
        partialMarCommand = self.makeCmd + ['-C',
                                            'tools/update-packaging', 'partial-patch',
                                            'STAGE_DIR=../../dist/update',
                                            'SRC_BUILD=../../previous',
                                            WithProperties('SRC_BUILD_ID=%(previous_buildid)s'),
                                            'DST_BUILD=../../current',
                                            WithProperties('DST_BUILD_ID=%(buildid)s')]
        if extraArgs is not None:
            partialMarCommand.extend(extraArgs)
        self.addStep(ShellCommand(
            name='rm_existing_partial_mar',
            description=['rm', 'existing', 'partial', 'mar'],
            doStepIf=self.previousMarExists,
            command=['bash', '-c', 'rm -rf *.partial.*.mar'],
            env=self.env,
            workdir='%s/dist/update' % self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        self.addStep(MockCommand(
            name='make_partial_mar',
            description=self.makeCmd + ['partial', 'mar'],
            doStepIf=self.previousMarExists,
            command=partialMarCommand,
            env=updateEnv,
            workdir=self.absMozillaObjDir,
            flunkOnFailure=True,
            haltOnFailure=False,
            mock=self.use_mock,
            target=self.mock_target,
        ))
        self.addStep(ShellCommand(
            name='rm_previous_mar',
            description=['rm', 'previous', 'mar'],
            doStepIf=self.previousMarExists,
            command=['rm', '-rf', 'previous.mar'],
            env=self.env,
            workdir='%s/dist/update' % self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        # Update the build properties to pickup information about the partial.
        self.addFilePropertiesSteps(
            filename='*.partial.*.mar',
            doStepIf=self.previousMarExists,
            directory='%s/dist/update' % self.absMozillaObjDir,
            fileType='partialMar',
            haltOnFailure=True,
        )

    def addCreateUpdateSteps(self):
        self.addStep(ShellCommand(
            name='rm_existing_mars',
            command=['bash', '-c', 'rm -rf *.mar'],
            env=self.env,
            workdir='%s/dist/update' % self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        # Run the parent steps to generate the complete mar.
        MercurialBuildFactory.addCreateUpdateSteps(self)
        if self.createPartial:
            self.addCreatePartialUpdateSteps()

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        upload_vars = []
        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_TO_TEMP': '1'})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        # Always upload builds to the dated tinderbox builds directories
        if self.tinderboxBuildsDir is None:
            tinderboxBuildsDir = "%s-%s" % (
                self.branchName, self.stagePlatform)
        else:
            tinderboxBuildsDir = self.tinderboxBuildsDir

        uploadArgs = dict(
            upload_dir=tinderboxBuildsDir,
            product=self.stageProduct,
            buildid=WithProperties("%(buildid)s"),
            revision=WithProperties("%(got_revision)s"),
            as_list=False,
        )
        uploadArgs['to_tinderbox_dated'] = True

        if self.nightly:
            uploadArgs['to_dated'] = True
            if 'st-an' in self.complete_platform or 'dbg' in self.complete_platform or 'asan' in self.complete_platform:
                uploadArgs['to_latest'] = False
            else:
                uploadArgs['to_latest'] = True
            if self.post_upload_include_platform:
                # This was added for bug 557260 because of a requirement for
                # mobile builds to upload in a slightly different location
                uploadArgs['branch'] = '%s-%s' % (
                    self.branchName, self.stagePlatform)
            else:
                uploadArgs['branch'] = self.branchName
        if uploadMulti:
            upload_vars.append("AB_CD=multi")
        if postUploadBuildDir:
            uploadArgs['builddir'] = postUploadBuildDir
        uploadEnv['POST_UPLOAD_CMD'] = postUploadCmdPrefix(**uploadArgs)

        objdir = WithProperties(
            '%(basedir)s/' + self.baseWorkDir + '/' + self.objdir)
        if self.platform.startswith('win'):
            objdir = '%s/%s' % (self.baseWorkDir, self.objdir)
        self.addStep(RetryingMockProperty(
            name='make_upload',
            command=self.makeCmd + ['upload'] + upload_vars,
            env=uploadEnv,
            workdir=objdir,
            extract_fn=parse_make_upload,
            haltOnFailure=True,
            description=self.makeCmd + ['upload'],
            mock=self.use_mock,
            target=self.mock_target,
            mock_workdir_prefix=None,
            timeout=40 * 60,  # 40 minutes
            log_eval_func=lambda c, s: regex_log_evaluator(
                c, s, upload_errors),
            locks=[upload_lock.access('counting')],
        ))

        def getPartialInfo(build):
            return [{
                "from_buildid": build.getProperty("previous_buildid"),
                "size": build.getProperty("partialMarSize"),
                "hash": build.getProperty("partialMarHash"),
                "url": build.getProperty("partialMarUrl"),
            }]

        self.addStep(SetBuildProperty(
            property_name="partialInfo",
            value=getPartialInfo,
            doStepIf=self.previousMarExists,
            haltOnFailure=True,
        ))

        if self.profiledBuild:
            talosBranch = "%s-%s-pgo-talos" % (
                self.branchName, self.complete_platform)
        else:
            talosBranch = "%s-%s-talos" % (
                self.branchName, self.complete_platform)

        sendchange_props = {
            'buildid': WithProperties('%(buildid:-)s'),
            'builduid': WithProperties('%(builduid:-)s'),
        }
        if self.nightly:
            sendchange_props['nightly_build'] = True
        # Not sure if this having this property is useful now
        # but it is
        if self.profiledBuild:
            sendchange_props['pgo_build'] = True
        else:
            sendchange_props['pgo_build'] = False

        if not uploadMulti:
            for master, warn, retries in self.talosMasters:
                self.addStep(SendChangeStep(
                             name='sendchange_%s' % master,
                             warnOnFailure=warn,
                             master=master,
                             retries=retries,
                             branch=talosBranch,
                             revision=WithProperties("%(got_revision)s"),
                             files=[WithProperties('%(packageUrl)s')],
                             user="sendchange",
                             comments=WithProperties('%(comments:-)s'),
                             sendchange_props=sendchange_props,
                             env=self.env,
                             ))

            files = [WithProperties('%(packageUrl)s')]
            files.append(WithProperties('%(testsUrl)s'))

            if self.packageTests:
                for master, warn, retries in self.unittestMasters:
                    self.addStep(SendChangeStep(
                                 name='sendchange_%s' % master,
                                 warnOnFailure=warn,
                                 master=master,
                                 retries=retries,
                                 branch=self.unittestBranch,
                                 revision=WithProperties("%(got_revision)s"),
                                 files=files,
                                 user="sendchange-unittest",
                                 comments=WithProperties('%(comments:-)s'),
                                 sendchange_props=sendchange_props,
                                 env=self.env,
                                 ))


class ReleaseBuildFactory(MercurialBuildFactory):
    def __init__(
        self, env, version, buildNumber, partialUpdates, ftpServer, brandName=None,
            unittestMasters=None, unittestBranch=None, talosMasters=None,
            usePrettyNames=True, enableUpdatePackaging=True, appVersion=None,
            bucketPrefix=None, **kwargs):
        self.version = version
        self.buildNumber = buildNumber
        self.partialUpdates = partialUpdates
        self.ftpServer = ftpServer
        self.talosMasters = talosMasters or []
        self.unittestMasters = unittestMasters or []
        self.unittestBranch = unittestBranch
        self.enableUpdatePackaging = enableUpdatePackaging
        if self.unittestMasters:
            assert self.unittestBranch

        if brandName:
            self.brandName = brandName
        else:
            self.brandName = kwargs['productName'].capitalize()
        self.bucketPrefix = bucketPrefix
        self.UPLOAD_EXTRA_FILES = []
        # Copy the environment to avoid screwing up other consumers of
        # MercurialBuildFactory
        env = env.copy()
        # Make sure MOZ_PKG_PRETTYNAMES is on and override MOZ_PKG_VERSION
        # The latter is only strictly necessary for RCs.
        if usePrettyNames:
            env['MOZ_PKG_PRETTYNAMES'] = '1'
        if appVersion is None or self.version != appVersion:
            env['MOZ_PKG_VERSION'] = version
        self.update_dir = 'update/%s/en-US' % getPlatformFtpDir(kwargs['platform'])
        self.current_mar_name = '%s-%s.complete.mar' % (kwargs['productName'],
                                                        self.version)
        MercurialBuildFactory.__init__(self, env=env, **kwargs)

    def getPackageFilename(self, platform, platform_variation):
        # Returning a non-True value prevents file properties steps from
        # running, which we don't need anything from (and don't work for
        # release builds in the first place)
        return False

    def getInstallerFilename(self):
        # Returning a non-True value prevents file properties steps from
        # running, which we don't need anything from (and don't work for
        # release builds in the first place)
        return False

    def addCreatePartialUpdateSteps(self):
        updateEnv = self.env.copy()
        # need to use absolute paths since mac may have difeerent layout
        # objdir/arch/dist vs objdir/dist
        updateEnv['MAR'] = WithProperties(
            '%(basedir)s' + '/%s/dist/host/bin/mar' % self.absMozillaObjDir)
        updateEnv['MBSDIFF'] = WithProperties(
            '%(basedir)s' + '/%s/dist/host/bin/mbsdiff' % self.absMozillaObjDir)
        mar_unpack_cmd = WithProperties(
            '%(basedir)s' + '/%s/tools/update-packaging/unwrap_full_update.pl' % self.absMozillaSrcDir)
        partial_mar_cmd = WithProperties(
            '%(basedir)s' + '/%s/tools/update-packaging/make_incremental_update.sh' % self.absMozillaSrcDir)

        self.addStep(ShellCommand(
            name='rm_current_unpack_dir',
            command=['rm', '-rf', 'current', 'current.work'],
            env=updateEnv,
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        self.addStep(ShellCommand(
            name='make_current_unpack_dir',
            command=['mkdir', 'current'],
            env=updateEnv,
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        self.addStep(MockCommand(
            name='unpack_current_mar',
            command=['perl', mar_unpack_cmd,
                     '../dist/%s/%s' % (self.update_dir, self.current_mar_name)],
            env=updateEnv,
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
            workdir='%s/current' % self.absMozillaObjDir,
        ))

        for oldVersion in self.partialUpdates:
            oldBuildNumber = self.partialUpdates[oldVersion]['buildNumber']

            previous_mar_name = '%s-%s.complete.mar' % (self.productName,
                                                        oldVersion)
            partial_mar_name = '%s-%s-%s.partial.mar' % \
                (self.productName, oldVersion, self.version)
            oldCandidatesDir = makeCandidatesDir(
                self.productName, oldVersion, oldBuildNumber,
                protocol='http', server=self.ftpServer)
            previousMarURL = '%supdate/%s/en-US/%s' % \
                (oldCandidatesDir, getPlatformFtpDir(self.platform),
                 previous_mar_name)

            self.addStep(ShellCommand(
                name='rm_previous_unpack_dir',
                command=['rm', '-rf', 'previous'],
                env=updateEnv,
                workdir=self.absMozillaObjDir,
                haltOnFailure=True,
            ))
            self.addStep(ShellCommand(
                name='make_previous_unpack_dir',
                command=['mkdir', 'previous'],
                env=updateEnv,
                workdir=self.absMozillaObjDir,
                haltOnFailure=True,
            ))
            self.addStep(RetryingMockCommand(
                name='get_previous_mar',
                description=['get', 'previous', 'mar'],
                command=[
                    'wget', '-O', 'previous.mar', '--no-check-certificate',
                    previousMarURL],
                mock=self.use_mock,
                target=self.mock_target,
                workdir='%s/dist' % self.absMozillaObjDir,
                haltOnFailure=True,
            ))
            self.addStep(MockCommand(
                name='unpack_previous_mar',
                description=['unpack', 'previous', 'mar'],
                command=['perl', mar_unpack_cmd, '../dist/previous.mar'],
                env=updateEnv,
                mock=self.use_mock,
                target=self.mock_target,
                workdir='%s/previous' % self.absMozillaObjDir,
                haltOnFailure=True,
            ))
            for dir in ['current', 'previous']:
                self.addStep(MockCommand(
                    name='remove pgc files (%s)' % dir,
                    command="find . -name \*.pgc -print -delete",
                    env=updateEnv,
                    mock=self.use_mock,
                    target=self.mock_target,
                    workdir="%s/%s" % (self.absMozillaObjDir, dir),
                    flunkOnFailure=False,
                    haltOnFailure=False,
                ))
            self.addStep(MockCommand(
                name='make_partial_mar',
                description=self.makeCmd + ['partial', 'mar'],
                command=['bash', partial_mar_cmd,
                         '%s/%s' % (self.update_dir, partial_mar_name),
                         '../previous', '../current'],
                env=updateEnv,
                mock=self.use_mock,
                target=self.mock_target,
                workdir='%s/dist' % self.absMozillaObjDir,
                haltOnFailure=True,
            ))
            # the previous script exits 0 always, need to check if mar exists
            self.addStep(ShellCommand(
                name='check_partial_mar',
                description=['check', 'partial', 'mar'],
                command=['ls', '-l',
                         '%s/%s' % (self.update_dir, partial_mar_name)],
                workdir='%s/dist' % self.absMozillaObjDir,
                        haltOnFailure=True,
            ))
            self.UPLOAD_EXTRA_FILES.append(
                '%s/%s' % (self.update_dir, partial_mar_name))
            if self.enableSigning and self.signingServers:
                partial_mar_path = '%s/dist/%s/%s' % \
                    (self.absMozillaObjDir, self.update_dir, partial_mar_name)
                cmd = '%s -f mar "%s"' % (self.signing_command, partial_mar_path)
                self.addStep(MockCommand(
                    name='sign_partial_mar',
                    description=['sign', 'partial', 'mar'],
                    command=['bash', '-c', WithProperties(cmd)],
                    env=updateEnv,
                    mock=self.use_mock,
                    target=self.mock_target,
                    workdir='.',
                    haltOnFailure=True,
                ))
                self.UPLOAD_EXTRA_FILES.append('%s/%s.asc' % (self.update_dir,
                                                              partial_mar_name))

            self.addFilePropertiesSteps(
                filename=partial_mar_name,
                directory='%s/dist/%s' % (self.absMozillaObjDir, self.update_dir),
                fileType='%sMar' % oldVersion,
                haltOnFailure=True,
            )
            self.addStep(SetBuildProperty(
                property_name="%sBuildNumber" % oldVersion,
                value=oldBuildNumber,
                haltOnFailure=True,
            ))

        def getPartialInfo(build, oldVersions):
            partials = []
            for v in oldVersions:
                partials.append({
                    "previousVersion": v,
                    "previousBuildNumber": build.getProperty("%sBuildNumber" % v),
                    "size": build.getProperty("%sMarSize" % v),
                    "hash": build.getProperty("%sMarHash" % v),
                })
            return partials

        self.addStep(SetBuildProperty(
            property_name="partialInfo",
            value=lambda b: getPartialInfo(b, self.partialUpdates.keys()),
            haltOnFailure=True,
        ))

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        info_txt = '%s_info.txt' % self.complete_platform
        self.UPLOAD_EXTRA_FILES.append(info_txt)
        # Make sure the complete MAR has been generated
        if self.enableUpdatePackaging:
            self.addStep(MockCommand(
                name='make_update_pkg',
                command=self.makeCmd + ['-C',
                         '%s/tools/update-packaging' % self.mozillaObjdir],
                env=self.env,
                haltOnFailure=True,
                mock=self.use_mock,
                target=self.mock_target,
                workdir='build',
            ))
            self.addFilePropertiesSteps(
                filename=self.current_mar_name,
                directory='%s/dist/%s' % (self.absMozillaObjDir, self.update_dir),
                fileType='completeMar',
                haltOnFailure=True,
            )
            if self.createPartial:
                self.addCreatePartialUpdateSteps()
        self.addStep(MockCommand(
            name='echo_buildID',
            command=['bash', '-c',
                     WithProperties('echo buildID=%(buildid)s > ' + info_txt)],
            workdir='build/%s/dist' % self.mozillaObjdir,
            mock=self.use_mock,
            target=self.mock_target,
        ))

        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_TO_TEMP': '1',
                          'UPLOAD_EXTRA_FILES': ' '.join(self.UPLOAD_EXTRA_FILES)})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        uploadArgs = dict(
            upload_dir="%s-%s" % (self.branchName, self.platform),
            product=self.productName,
            version=self.version,
            buildNumber=str(self.buildNumber),
            as_list=False)
        if self.enableSigning and self.signingServers:
            uploadArgs['signed'] = True
        upload_vars = []

        if self.productName == 'fennec':
            builddir = '%s/en-US' % self.stagePlatform
            if uploadMulti:
                builddir = '%s/multi' % self.stagePlatform
                upload_vars = ['AB_CD=multi']
            if postUploadBuildDir:
                builddir = '%s/%s' % (self.stagePlatform, postUploadBuildDir)
            uploadArgs['builddir'] = builddir
            uploadArgs['to_mobile_candidates'] = True
            uploadArgs['nightly_dir'] = 'candidates'
            uploadArgs['product'] = 'mobile'
        else:
            uploadArgs['to_candidates'] = True

        if self.bucketPrefix:
            uploadArgs['bucket_prefix'] = self.bucketPrefix
        uploadEnv['POST_UPLOAD_CMD'] = postUploadCmdPrefix(**uploadArgs)

        objdir = WithProperties('%(basedir)s/build/' + self.objdir)
        if self.platform.startswith('win'):
            objdir = 'build/%s' % self.objdir
        self.addStep(RetryingMockProperty(
                     name='make_upload',
                     command=self.makeCmd + ['upload'] + upload_vars,
                     env=uploadEnv,
                     workdir=objdir,
                     extract_fn=parse_make_upload,
                     haltOnFailure=True,
                     description=['upload'],
                     timeout=60 * 60,  # 60 minutes
                     log_eval_func=lambda c, s: regex_log_evaluator(
                         c, s, upload_errors),
                     target=self.mock_target,
                     mock=self.use_mock,
                     mock_workdir_prefix=None,
                     ))

        # Send to the "release" branch on talos, it will do
        # super-duper-extra testing
        talosBranch = "release-%s-%s-talos" % (
            self.branchName, self.complete_platform)
        sendchange_props = {
            'buildid': WithProperties('%(buildid:-)s'),
            'builduid': WithProperties('%(builduid:-)s'),
        }
        for master, warn, retries in self.talosMasters:
            self.addStep(SendChangeStep(
                         name='sendchange_%s' % master,
                         warnOnFailure=warn,
                         master=master,
                         retries=retries,
                         branch=talosBranch,
                         revision=WithProperties("%(got_revision)s"),
                         files=[WithProperties('%(packageUrl)s')],
                         user="sendchange",
                         comments=WithProperties('%(comments:-)s'),
                         sendchange_props=sendchange_props,
                         env=self.env,
                         ))

        for master, warn, retries in self.unittestMasters:
            self.addStep(SendChangeStep(
                         name='sendchange_%s' % master,
                         warnOnFailure=warn,
                         master=master,
                         retries=retries,
                         branch=self.unittestBranch,
                         revision=WithProperties("%(got_revision)s"),
                         files=[WithProperties('%(packageUrl)s'),
                                WithProperties('%(testsUrl)s')],
                         user="sendchange-unittest",
                         comments=WithProperties('%(comments:-)s'),
                         sendchange_props=sendchange_props,
                         env=self.env,
                         ))
        if self.balrog_api_root:
            self.submitBalrogUpdates(type_='release')


def identToProperties(default_prop=None):
    '''Create a method that is used in a SetProperty step to map the
    output of make ident to build properties.

    To be backwards compat, this allows for a property name to be specified
    to be used for a single hg revision.
    '''
    def list2dict(rv, stdout, stderr):
        props = {}
        stdout = stdout.strip()
        if default_prop is not None and re.match(r'[0-9a-f]{12}\+?', stdout):
            # a single hg version
            props[default_prop] = stdout
        else:
            for l in filter(None, stdout.split('\n')):
                e = filter(None, l.split())
                props[e[0]] = e[1]
        return props
    return list2dict


class BaseRepackFactory(MozillaBuildFactory, TooltoolMixin):
    # Override ignore_dirs so that we don't delete l10n nightly builds
    # before running a l10n nightly build
    ignore_dirs = MozillaBuildFactory.ignore_dirs + [normalizeName('*-nightly')]

    extraConfigureArgs = []

    def __init__(self, project, appName, l10nRepoPath,
                 compareLocalesRepoPath, compareLocalesTag, stageServer,
                 stageUsername, mozconfig, stageSshKey=None, objdir='',
                 platform='',
                 tree="notset", mozillaDir=None, mozillaSrcDir=None,
                 l10nTag='default',
                 mergeLocales=True,
                 testPrettyNames=False,
                 callClientPy=False,
                 clientPyConfig=None,
                 tooltool_manifest_src=None,
                 tooltool_bootstrap="setup.sh",
                 tooltool_url_list=None,
                 tooltool_script=None,
                 **kwargs):
        MozillaBuildFactory.__init__(self, **kwargs)

        self.platform = platform
        self.project = project
        self.productName = project
        self.appName = appName
        self.l10nRepoPath = l10nRepoPath
        self.l10nTag = l10nTag
        self.compareLocalesRepoPath = compareLocalesRepoPath
        self.compareLocalesTag = compareLocalesTag
        self.mergeLocales = mergeLocales
        self.stageServer = stageServer
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey
        self.tree = tree
        self.mozconfig = mozconfig
        self.testPrettyNames = testPrettyNames
        self.callClientPy = callClientPy
        self.clientPyConfig = clientPyConfig
        self.tooltool_manifest_src = tooltool_manifest_src
        self.tooltool_url_list = tooltool_url_list or []
        self.tooltool_script = tooltool_script or ['/tools/tooltool.py']
        self.tooltool_bootstrap = tooltool_bootstrap

        assert len(self.tooltool_url_list) <= 1, "multiple urls not currently supported by tooltool"

        self.addStep(SetBuildProperty(
                     property_name='tree',
                     value=self.tree,
                     haltOnFailure=True
                     ))

        self.origSrcDir = self.branchName

        if self.enable_pymake:
            self.makeCmd = ['python', WithProperties("%(basedir)s/build/" + "%s/build/pymake/make.py" % self.origSrcDir)]
        else:
            self.makeCmd = ['make']

        self.linkTools = False

        # Mozilla subdir
        if mozillaDir:
            self.mozillaDir = '/%s' % mozillaDir
            self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, mozillaDir)
            self.linkTools = True
        else:
            self.mozillaDir = ''
            # Thunderbird doesn't have a mozilla in the objdir, but it
            # still does for the srcdir.
            if mozillaSrcDir:
                self.linkTools = True
                self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, mozillaSrcDir)
            else:
                self.mozillaSrcDir = self.origSrcDir

        # self.mozillaObjdir is used in SeaMonkey's and Thunderbird's case
        self.objdir = objdir or self.origSrcDir
        self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)

        self.absSrcDir = "%s/%s" % (self.baseWorkDir,
                                    self.origSrcDir)
        self.absObjDir = '%s/%s' % (self.absSrcDir,
                                    self.objdir)

        # These following variables are useful for sharing build steps (e.g.
        # update generation) from classes that use object dirs (e.g. nightly
        # repacks).
        #
        # We also concatenate the baseWorkDir at the outset to avoid having to
        # do that everywhere.
        self.absMozillaSrcDir = "%s/%s" % (
            self.baseWorkDir, self.mozillaSrcDir)
        self.absMozillaObjDir = '%s/%s/%s' % (
            self.baseWorkDir, self.origSrcDir, self.mozillaObjdir)

        self.latestDir = '/pub/mozilla.org/%s' % self.productName + \
                         '/nightly/latest-%s-l10n' % self.branchName

        if objdir != '':
            # L10NBASEDIR is relative to MOZ_OBJDIR
            self.env.update({'MOZ_OBJDIR': WithProperties('%(basedir)s/' + self.absObjDir),
                             'L10NBASEDIR': '../../l10n'})

        if platform == 'macosx64':
            # use "mac" instead of "mac64" for macosx64
            self.env.update({'MOZ_PKG_PLATFORM': 'mac'})

        self.uploadEnv = self.env.copy(
        )  # pick up any env variables in our subclass
        self.uploadEnv.update({
            'AB_CD': WithProperties('%(locale)s'),
            'UPLOAD_HOST': stageServer,
            'UPLOAD_USER': stageUsername,
            'UPLOAD_TO_TEMP': '1',
            'POST_UPLOAD_CMD': self.postUploadCmd  # defined in subclasses
        })
        if stageSshKey:
            self.uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % stageSshKey

        self.preClean()

        # Need to override toolsdir as set by MozillaBuildFactory because
        # we need Windows-style paths.
        if self.platform.startswith('win'):
            self.addStep(SetProperty(
                command=['bash', '-c', 'pwd -W'],
                property='toolsdir',
                workdir='tools'
            ))

        if self.use_mock:
            MercurialBuildFactory.addMockSteps(self)

        self.addStep(ShellCommand(
                     name='mkdir_l10n',
                     command=['sh', '-c', 'mkdir -p l10n'],
                     descriptionDone='mkdir l10n',
                     workdir=self.baseWorkDir,
                     flunkOnFailure=False
                     ))

        # call out to overridable functions
        self.getSources()
        self.updateSources()
        self.getMozconfig()
        if self.tooltool_manifest_src:
            self.addTooltoolStep(workdir=self.absSrcDir)
        self.configure()
        self.tinderboxPrintBuildInfo()
        self.downloadBuilds()
        self.updateEnUS()
        self.tinderboxPrintRevisions()
        self.compareLocalesSetup()
        self.compareLocales()
        if self.signingServers and self.enableSigning:
            self.addGetTokenSteps()
        self.doRepack()
        self.doUpload()
        if self.testPrettyNames:
            self.doTestPrettyNames()

    def processCommand(self, **kwargs):
        '''This function is overriden by MaemoNightlyRepackFactory to
        adjust the command and workdir approprietaly for mock
        '''
        return kwargs

    def getMozconfig(self):
        self.addStep(ShellCommand(
            name='get_mozconfig',
            command=['cp', self.mozconfig, '.mozconfig'],
            description=['getting', 'mozconfig'],
            descriptionDone=['got', 'mozconfig'],
            workdir='build/' + self.origSrcDir,
            haltOnFailure=True,
        ))
        self.addStep(ShellCommand(
            name='cat_mozconfig',
            command=['cat', '.mozconfig'],
            workdir='build/' + self.origSrcDir
        ))

    def configure(self):
        if self.platform.startswith('win'):
            self.addStep(SetProperty(
                command=['bash', '-c', 'pwd -W'],
                property='basedir',
                workdir='.'
            ))
        if self.linkTools:
            self.addStep(MockCommand(**self.processCommand(
                name='link_tools',
                env=self.env,
                command=['sh', '-c', 'if [ ! -e tools ]; then ln -s ../tools; fi'],
                workdir='build',
                description=['link tools'],
                haltOnFailure=True,
                mock=self.use_mock,
                target=self.mock_target,
            )))
        self.addStep(MockCommand(**self.processCommand(
                                 name='make_configure',
                                 env=self.env,
                                 command=self.makeCmd + ['-f', 'client.mk', 'configure'],
                                 workdir=self.absSrcDir,
                                 description=['make config'],
                                 haltOnFailure=True,
                                 mock=self.use_mock,
                                 target=self.mock_target,
                                 )))
        self.addStep(MockCommand(**self.processCommand(
            name='rm_CLOBBER_files',
            env=self.env,
            command=['rm', '-rf', '%s/CLOBBER' % self.absMozillaObjDir,
                     '%s/CLOBBER' % self.absMozillaSrcDir],
            workdir='.',
            description=['remove CLOBBER files'],
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
            )))
        self.addStep(MockCommand(**self.processCommand(
                                 name='compile_nsinstall',
                                 env=self.env,
                                 command=self.makeCmd + ['export'],
                                 workdir='%s/config' % self.absMozillaObjDir,
                                 description=['compile nsinstall'],
                                 haltOnFailure=True,
                                 mock=self.use_mock,
                                 target=self.mock_target,
                                 )))

    def tinderboxPrint(self, propName, propValue):
        self.addStep(OutputStep(
                     name='tinderboxprint_%s' % propName,
                     data=['TinderboxPrint:',
                           '%s:' % propName,
                           propValue]
                     ))

    def tinderboxPrintBuildInfo(self):
        '''Display some build properties for scraping in Tinderbox.
        '''
        self.tinderboxPrint('locale', WithProperties('%(locale)s'))
        self.tinderboxPrint('tree', self.tree)
        self.tinderboxPrint('buildnumber', WithProperties('%(buildnumber)s'))

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        self.addStep(RetryingMockProperty(
                     name='make_upload',
                     command=self.makeCmd + ['upload',
                                             WithProperties(
                                                 'AB_CD=%(locale)s')],
                     env=self.uploadEnv,
                     workdir='%s/%s/locales' % (self.absObjDir, self.appName),
                     haltOnFailure=True,
                     flunkOnFailure=True,
                     log_eval_func=lambda c, s: regex_log_evaluator(
                         c, s, upload_errors),
                     locks=[upload_lock.access('counting')],
                     extract_fn=parse_make_upload,
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))

        def getPartialInfo(build):
            return [{
                "from_buildid": build.getProperty("previous_buildid"),
                "size": build.getProperty("partialMarSize"),
                "hash": build.getProperty("partialMarHash"),
                "url": build.getProperty("partialMarUrl"),
            }]

        self.addStep(SetBuildProperty(
            property_name="partialInfo",
            value=getPartialInfo,
            doStepIf=self.previousMarExists,
            haltOnFailure=True,
        ))

    def getSources(self):
        step = self.makeHgtoolStep(
            name='get_enUS_src',
            wc=self.origSrcDir,
            rev=WithProperties("%(en_revision)s"),
            locks=[hg_l10n_lock.access('counting')],
            workdir=self.baseWorkDir,
            use_properties=False,
        )
        self.addStep(step)

        mirrors = []
        if self.baseMirrorUrls:
            mirrors = [WithProperties(url + "/" + self.l10nRepoPath + "/%(locale)s") for url in self.baseMirrorUrls]
        step = self.makeHgtoolStep(
            name='get_locale_src',
            rev=WithProperties("%(l10n_revision)s"),
            repo_url=WithProperties("https://" + self.hgHost + "/" +
                                    self.l10nRepoPath + "/%(locale)s"),
            workdir='%s/l10n' % self.baseWorkDir,
            locks=[hg_l10n_lock.access('counting')],
            use_properties=False,
            mirrors=mirrors,
        )
        self.addStep(step)

        if self.callClientPy:
            self.addClientPySteps()

    def addClientPySteps(self):
        c = self.clientPyConfig

        # build up the checkout command with all options
        skipBlankRepos = c.get('skip_blank_repos', False)
        co_command = ['python', 'client.py', 'checkout']
        if c.get('moz_repo_path'):
            co_command.append('--mozilla-repo=%s' %
                              self.getRepository(c.get('moz_repo_path')))
        if c.get('inspector_repo_path'):
            co_command.append('--inspector-repo=%s' % self.getRepository(
                c.get('inspector_repo_path')))
        elif skipBlankRepos:
            co_command.append('--skip-inspector')
        if c.get('venkman_repo_path'):
            co_command.append('--venkman-repo=%s' %
                              self.getRepository(c.get('venkman_repo_path')))
        elif skipBlankRepos:
            co_command.append('--skip-venkman')
        if c.get('chatzilla_repo_path'):
            co_command.append('--chatzilla-repo=%s' % self.getRepository(
                c.get('chatzilla_repo_path')))
        elif skipBlankRepos:
            co_command.append('--skip-chatzilla')
        if c.get('cvsroot'):
            co_command.append('--cvsroot=%s' % c.get('cvsroot'))

        buildRevision = c.get('buildRevision', None)
        if buildRevision:
            co_command.append('--comm-rev=%s' % buildRevision)
            co_command.append('--mozilla-rev=%s' % buildRevision)
            co_command.append('--inspector-rev=%s' % buildRevision)
            co_command.append('--venkman-rev=%s' % buildRevision)
            co_command.append('--chatzilla-rev=%s' % buildRevision)

        # execute the checkout
        self.addStep(ShellCommand,
                     command=co_command,
                     description=['running', 'client.py', 'checkout'],
                     descriptionDone=['client.py', 'checkout'],
                     haltOnFailure=True,
                     workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir),
                     timeout=60 * 60  # 1 hour
                     )

    def updateEnUS(self):
        '''Update the en-US source files to the revision used by
        the repackaged build.

        This is implemented in the subclasses.
        '''
        pass

    def tinderboxPrintRevisions(self):
        '''Display the various revisions used in building for
        scraping in Tinderbox.
        This is implemented in the subclasses.
        '''
        pass

    def compareLocalesSetup(self):
        compareLocalesRepo = self.getRepository(self.compareLocalesRepoPath)
        self.addStep(ShellCommand(
                     name='rm_compare_locales',
                     command=['rm', '-rf', 'compare-locales'],
                     description=['remove', 'compare-locales'],
                     workdir=self.baseWorkDir,
                     haltOnFailure=True
                     ))
        self.addStep(MercurialCloneCommand(
                     name='clone_compare_locales',
                     command=['hg', 'clone',
                              compareLocalesRepo, 'compare-locales'],
                     description=['checkout', 'compare-locales'],
                     workdir=self.baseWorkDir,
                     haltOnFailure=True
                     ))
        self.addStep(ShellCommand(
                     name='update_compare_locales',
                     command=['hg', 'up', '-C', '-r', self.compareLocalesTag],
                     description='update compare-locales',
                     workdir='%s/compare-locales' % self.baseWorkDir,
                     haltOnFailure=True
                     ))

    def compareLocales(self):
        if self.mergeLocales:
            mergeLocaleOptions = ['-m',
                                  WithProperties('%(basedir)s/' +
                                                 "%s/merged" % self.baseWorkDir)]
            flunkOnFailure = False
            haltOnFailure = False
            warnOnFailure = True
        else:
            mergeLocaleOptions = []
            flunkOnFailure = True
            haltOnFailure = True
            warnOnFailure = False
        self.addStep(ShellCommand(
                     name='rm_merged',
                     command=['rm', '-rf', 'merged'],
                     description=['remove', 'merged'],
                     workdir=self.baseWorkDir,
                     haltOnFailure=True
                     ))
        self.addStep(ShellCommand(
                     name='run_compare_locales',
                     command=['python',
                              '../../../compare-locales/scripts/compare-locales'] +
                     mergeLocaleOptions +
                     ["l10n.ini",
                      "../../../l10n",
                      WithProperties('%(locale)s')],
                     description='comparing locale',
                     env={'PYTHONPATH': ['../../../compare-locales/lib']},
                     flunkOnFailure=flunkOnFailure,
                     warnOnFailure=warnOnFailure,
                     haltOnFailure=haltOnFailure,
                     workdir="%s/%s/locales" % (self.absSrcDir,
                                                self.appName),
                     ))

    def doRepack(self):
        '''Perform the repackaging.

        This is implemented in the subclasses.
        '''
        pass

    def preClean(self):
        self.addStep(ShellCommand(
                     name='rm_dist_upload',
                     command=['sh', '-c',
                              'if [ -d ' + self.absMozillaObjDir + '/dist/upload ]; then ' +
                              'rm -rf ' + self.absMozillaObjDir + '/dist/upload; ' +
                              'fi'],
                     description="rm dist/upload",
                     workdir='.',
                     haltOnFailure=True
                     ))

        self.addStep(ShellCommand(
                     name='rm_dist_update',
                     command=['sh', '-c',
                              'if [ -d ' + self.absMozillaObjDir + '/dist/update ]; then ' +
                              'rm -rf ' + self.absMozillaObjDir + '/dist/update; ' +
                              'fi'],
                     description="rm dist/update",
                     workdir='.',
                     haltOnFailure=True
                     ))

    def doTestPrettyNames(self):
        # Need to re-download this file because it gets removed earlier
        self.addStep(MockCommand(
                     name='wget_enUS',
                     command=self.makeCmd + ['wget-en-US'],
                     description='wget en-US',
                     env=self.env,
                     haltOnFailure=True,
                     workdir='%s/%s/locales' % (self.absMozillaObjDir, self.appName),
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        self.addStep(MockCommand(
                     name='make_unpack',
                     command=self.makeCmd + ['unpack'],
                     description='unpack en-US',
                     haltOnFailure=True,
                     env=self.env,
                     workdir='%s/%s/locales' % (self.absMozillaObjDir, self.appName),
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        # We need to override ZIP_IN because it defaults to $(PACKAGE), which
        # will be the pretty name version here.
        self.addStep(MockProperty(
                     command=self.makeCmd + ['--no-print-directory',
                                             'echo-variable-ZIP_IN'],
                     property='zip_in',
                     env=self.env,
                     workdir='%s/%s/locales' % (self.absMozillaObjDir, self.appName),
                     haltOnFailure=True,
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        prettyEnv = self.env.copy()
        prettyEnv['MOZ_PKG_PRETTYNAMES'] = '1'
        prettyEnv['ZIP_IN'] = WithProperties('%(zip_in)s')

        if self.productName == 'thunderbird' and self.platform.startswith('macosx'):
            # This is a hack to get Thunderbird mac repacks working again and
            # it should likely be checked if this code also works with all
            # products. See bug 1231174 for more details.
            prettyEnv.update({'MOZ_CURRENT_PROJECT': os.path.basename(self.objdir)})

        if self.platform.startswith('win'):
            self.addStep(MockProperty(
                         command=self.makeCmd + ['--no-print-directory',
                                                 'echo-variable-WIN32_INSTALLER_IN'],
                         property='win32_installer_in',
                         env=self.env,
                         workdir='%s/%s/locales' % (self.absMozillaObjDir, self.appName),
                         haltOnFailure=True,
                         mock=self.use_mock,
                         target=self.mock_target,
                         ))
            prettyEnv['WIN32_INSTALLER_IN'] = WithProperties(
                '%(win32_installer_in)s')
        self.addStep(MockCommand(
                     name='repack_installers_pretty',
                     description=['repack', 'installers', 'pretty'],
                     command=self.makeCmd + [WithProperties('installers-%(locale)s'),
                                             WithProperties('LOCALE_MERGEDIR=%(basedir)s/' +
                                                            "%s/merged" % self.baseWorkDir)],
                     env=prettyEnv,
                     haltOnFailure=False,
                     flunkOnFailure=False,
                     warnOnFailure=True,
                     workdir='%s/%s/locales' % (self.absObjDir, self.appName),
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))


class NightlyRepackFactory(BaseRepackFactory, NightlyBuildFactory):
    extraConfigureArgs = []

    def __init__(self, enUSBinaryURL, nightly=False, env={},
                 updatePlatform=None, downloadBaseURL=None,
                 l10nNightlyUpdate=False, l10nDatedDirs=False,
                 createPartial=False, extraConfigureArgs=[], **kwargs):
        self.nightly = nightly
        self.l10nNightlyUpdate = l10nNightlyUpdate
        self.updatePlatform = updatePlatform
        self.downloadBaseURL = downloadBaseURL
        self.createPartial = createPartial
        self.extraConfigureArgs = extraConfigureArgs

        # This is required because this __init__ doesn't call the
        # NightlyBuildFactory __init__ where self.complete_platform
        # is set.  This is only used for android, which doesn't
        # use this factory, so is safe
        self.complete_platform = ''

        env = env.copy()

        env.update({'EN_US_BINARY_URL': enUSBinaryURL})

        # Unfortunately, we can't call BaseRepackFactory.__init__() before this
        # because it needs self.postUploadCmd set
        assert 'project' in kwargs
        assert 'repoPath' in kwargs

        # 1) upload preparation
        if 'branchName' in kwargs:
            uploadDir = '%s-l10n' % kwargs['branchName']
        else:
            uploadDir = '%s-l10n' % self.getRepoName(kwargs['repoPath'])

        uploadArgs = dict(
            product=kwargs['project'],
            branch=uploadDir,
            as_list=False,
        )
        if l10nDatedDirs:
            # nightly repacks and on-change upload to different places
            if self.nightly:
                uploadArgs['buildid'] = WithProperties("%(buildid)s")
                uploadArgs['to_latest'] = True
                uploadArgs['to_dated'] = True
            else:
                # For the repack-on-change scenario we just want to upload
                # to tinderbox builds
                uploadArgs['upload_dir'] = uploadDir
                uploadArgs['to_tinderbox_builds'] = True
        else:
            # for backwards compatibility when the nightly and repack on-change
            # runs were the same
            uploadArgs['to_latest'] = True

        self.postUploadCmd = postUploadCmdPrefix(**uploadArgs)

        # 2) preparation for updates
        if l10nNightlyUpdate and self.nightly:
            env.update({'MOZ_MAKE_COMPLETE_MAR': '1',
                        'DOWNLOAD_BASE_URL': '%s/nightly' % self.downloadBaseURL})
            if '--enable-update-packaging' not in self.extraConfigureArgs:
                self.extraConfigureArgs += ['--enable-update-packaging']

        BaseRepackFactory.__init__(self, env=env, **kwargs)

        if l10nNightlyUpdate:
            NightlyBuildFactory.submitBalrogUpdates(self)

        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def updateSources(self):
        self.addStep(ShellCommand(
                     name='update_locale_source',
                     command=['hg', 'up', '-C', '-r', self.l10nTag],
                     description='update workdir',
                     workdir=WithProperties('build/l10n/%(locale)s'),
                     haltOnFailure=True
                     ))
        self.addStep(SetProperty(
                     command=['hg', 'ident', '-i'],
                     haltOnFailure=True,
                     property='l10n_revision',
                     workdir=WithProperties('build/l10n/%(locale)s')
                     ))

    def downloadBuilds(self):
        if self.mozillaDir:
            workdir = '%s/%s/locales' % (self.absObjDir,
                                         self.appName)
        else:
            workdir = '%s/%s/locales' % (self.absMozillaObjDir,
                                         self.appName)
        self.addStep(RetryingMockCommand(
                     name='wget_enUS',
                     command=self.makeCmd + ['wget-en-US'],
                     descriptionDone='wget en-US',
                     env=self.env,
                     haltOnFailure=True,
                     workdir=workdir,
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))

    def updateEnUS(self):
        '''Update en-US to the source stamp we get from make ident.

        Requires that we run make unpack first.
        '''
        self.addStep(MockCommand(
                     name='make_unpack',
                     command=self.makeCmd + ['unpack'],
                     descriptionDone='unpacked en-US',
                     haltOnFailure=True,
                     env=self.env,
                     workdir='%s/%s/locales' % (self.absObjDir,
                                                self.appName),
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        self.addStep(MockProperty(
                     command=self.makeCmd + ['ident'],
                     haltOnFailure=True,
                     env=self.env,
                     workdir='%s/%s/locales' % (self.absObjDir,
                                                self.appName),
                     extract_fn=identToProperties(),
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        if self.clientPyConfig:
            self.addStep(MockCommand(
                         name='update_comm_enUS_revision',
                         command=['hg', 'update', '-C', '-r',
                                  WithProperties('%(comm_revision)s')],
                         haltOnFailure=True,
                         env=self.env,
                         workdir=self.absSrcDir,
                         mock=self.use_mock,
                         target=self.mock_target,
                         ))
            self.addStep(MockCommand(
                         name='update_mozilla_enUS_revision',
                         command=['hg', 'update', '-C', '-r',
                                  WithProperties('%(moz_revision)s')],
                         haltOnFailure=True,
                         env=self.env,
                         workdir=self.absMozillaSrcDir,
                         mock=self.use_mock,
                         target=self.mock_target,
                         ))
        else:
            self.addStep(MockCommand(
                         name='update_enUS_revision',
                         command=['hg', 'update', '-C', '-r',
                                  WithProperties('%(fx_revision)s')],
                         haltOnFailure=True,
                         env=self.env,
                         workdir=self.absMozillaSrcDir,
                         mock=self.use_mock,
                         target=self.mock_target,
                         ))

    def tinderboxPrintRevisions(self):
        if self.clientPyConfig:
            self.tinderboxPrint(
                'comm_revision', WithProperties('%(comm_revision)s'))
            self.tinderboxPrint(
                'moz_revision', WithProperties('%(moz_revision)s'))
            self.tinderboxPrint(
                'l10n_revision', WithProperties('%(l10n_revision)s'))
        else:
            self.tinderboxPrint(
                'fx_revision', WithProperties('%(fx_revision)s'))
            self.tinderboxPrint(
                'l10n_revision', WithProperties('%(l10n_revision)s'))

    def downloadMarTools(self):
        mar = 'mar'
        mbsdiff = 'mbsdiff'
        if self.platform.startswith('win'):
            mar += '.exe'
            mbsdiff += '.exe'

        baseURL = 'http://%s' % self.stageServer + \
                  '/pub/mozilla.org/%s' % self.productName + \
                  '/nightly/latest-%s' % self.branchName + \
                  '/mar-tools/%s' % self.platform
        marURL = '%s/%s' % (baseURL, mar)
        mbsdiffURL = '%s/%s' % (baseURL, mbsdiff)

        self.addStep(RetryingMockCommand(
            name='get_mar',
            description=['get', 'mar'],
            command=['bash', '-c',
                     '''if [ ! -f %s ]; then
                       wget -O  %s --no-check-certificate %s;
                     fi;
                     (test -e %s && test -s %s) || exit 1;
                     chmod 755 %s'''.replace("\n", "") % (mar, mar, marURL, mar, mar, mar)],
            workdir='%s/dist/host/bin' % self.absMozillaObjDir,
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
        ))
        self.addStep(RetryingMockCommand(
            name='get_mbsdiff',
            description=['get', 'mbsdiff'],
            command=['bash', '-c',
                     '''if [ ! -f %s ]; then
                       wget -O  %s --no-check-certificate %s;
                     fi;
                     (test -e %s && test -s %s) || exit 1;
                     chmod 755 %s'''.replace("\n", "") % (mbsdiff, mbsdiff, mbsdiffURL, mbsdiff, mbsdiff, mbsdiff)],
            workdir='%s/dist/host/bin' % self.absMozillaObjDir,
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
        ))

    # The parent class gets us most of the way there, we just need to add the
    # locale.
    def getCompleteMarPatternMatch(self):
        return '.%(locale)s.' + NightlyBuildFactory.getCompleteMarPatternMatch(self)

    def doRepack(self):
        self.downloadMarTools()

        installersEnv = self.env.copy()
        if self.productName == 'thunderbird' and self.platform.startswith('macosx'):
            # This is a hack to get Thunderbird mac repacks working again and
            # it should likely be checked if this code also works with all
            # products. See bug 1231174 for details.
            installersEnv.update({'MOZ_CURRENT_PROJECT': os.path.basename(self.objdir)})

        self.addStep(MockCommand(
                     name='repack_installers',
                     description=['repack', 'installers'],
                     command=self.makeCmd + [WithProperties('installers-%(locale)s'),
                                             WithProperties('LOCALE_MERGEDIR=%(basedir)s/' +
                                                            "%s/merged" % self.baseWorkDir)],
                     env=installersEnv,
                     haltOnFailure=True,
                     workdir='%s/%s/locales' % (self.absObjDir, self.appName),
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        self.addStep(FindFile(
            name='find_inipath',
            filename='application.ini',
            directory='dist/l10n-stage',
            filetype='file',
            max_depth=5,
            property_name='inipath',
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        printconfig_env = self.env.copy()
        printconfig_env.update({'TOOLTOOL_DIR': WithProperties('%(basedir)s/build')})
        del printconfig_env['MOZ_OBJDIR']
        printconfig_workdir = WithProperties('%(basedir)s/build/' + self.objdir)
        # hax https://bugzilla.mozilla.org/show_bug.cgi?id=1232466#c10
        if self.platform.startswith('win'):
            python = ['c:/mozilla-build/python27/python', '-u']
        else:
            python = ['/tools/buildbot/bin/python']
        if self.mozillaSrcDir:
            machPath = '%(basedir)s/build/mozilla/mach'
        else:
            machPath = '%(basedir)s/build/mach'
        # we need abs paths because we are in a non relative workdir
        printconfig_base_command = python + [
            WithProperties(machPath), 'python',
            # abs*Dir attrs lie. they are not absolute paths
            WithProperties('%(basedir)s/' + '%s/config/printconfigsetting.py' % self.absMozillaSrcDir),
            WithProperties('%(basedir)s/' + self.absMozillaObjDir + '%(inipath)s')
        ]
        self.addStep(SetProperty(
            command=printconfig_base_command + ['App', 'BuildID'],
            name='get_build_id',
            workdir=printconfig_workdir,
            env=printconfig_env,
            property='buildid',
        ))
        self.addStep(SetProperty(
            command=printconfig_base_command + ['App', 'Version'],
            property='appVersion',
            name='get_app_version',
            workdir=printconfig_workdir,
            env=printconfig_env,
        ))
        self.addStep(SetProperty(
            command=printconfig_base_command + ['App', 'Name'],
            property='appName',
            name='get_app_name',
            workdir=printconfig_workdir,
            env=printconfig_env,
        ))

        if self.l10nNightlyUpdate:
            self.addFilePropertiesSteps(filename='*.complete.mar',
                                        directory='%s/dist/update' % self.absMozillaObjDir,
                                        fileType='completeMar',
                                        haltOnFailure=True)

        # Remove the source (en-US) package so as not to confuse later steps
        # that look up build details.
        self.addStep(ShellCommand(name='rm_en-US_build',
                                  command=['bash', '-c', 'rm -rf *.en-US.*'],
                                  description=['remove', 'en-US', 'build'],
                                  env=self.env,
                                  workdir='%s/dist' % self.absMozillaObjDir,
                                  haltOnFailure=True)
                     )
        if self.l10nNightlyUpdate and self.createPartial:
            self.addCreatePartialUpdateSteps(
                extraArgs=[WithProperties('AB_CD=%(locale)s')])


class ReleaseFactory(MozillaBuildFactory):
    def getCandidatesDir(self, product, version, buildNumber,
                         nightlyDir="nightly"):
        # can be used with rsync, eg host + ':' + getCandidatesDir()
        # and "http://' + host + getCandidatesDir()
        return '/pub/mozilla.org/' + product + '/' + nightlyDir + '/' + \
               str(version) + '-candidates/build' + str(buildNumber) + '/'

    def getShippedLocales(self, sourceRepo, baseTag, appName):
        return '%s/raw-file/%s_RELEASE/%s/locales/shipped-locales' % \
            (sourceRepo, baseTag, appName)

    def getSshKeyOption(self, hgSshKey):
        if hgSshKey:
            return '-i %s' % hgSshKey
        return hgSshKey

    def makeLongVersion(self, version):
        version = re.sub('a([0-9]+)$', ' Alpha \\1', version)
        version = re.sub('b([0-9]+)$', ' Beta \\1', version)
        version = re.sub('rc([0-9]+)$', ' RC \\1', version)
        return version


class SingleSourceFactory(ReleaseFactory):
    def __init__(self,
                 productName,
                 version,
                 baseTag,
                 stagingServer,
                 stageUsername,
                 stageSshKey,
                 buildNumber,
                 mozconfig,
                 appVersion=None,
                 objdir='',
                 mozillaDir=None,
                 mozillaSrcDir=None,
                 autoconfDirs=['.'],
                 buildSpace=2,
                 bucketPrefix=None,
                 **kwargs):
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)

        self.mozconfig = mozconfig
        self.releaseTag = '%s_RELEASE' % (baseTag)
        self.origSrcDir = self.branchName

        # Mozilla subdir
        if mozillaDir:
            self.mozillaDir = '/%s' % mozillaDir
            self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, mozillaDir)
        else:
            self.mozillaDir = ''

            # Thunderbird now has a different srcdir to mozillaDir
            if mozillaSrcDir:
                self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, mozillaSrcDir)
            else:
                self.mozillaSrcDir = self.origSrcDir

        # self.mozillaObjdir is used in SeaMonkey's and Thunderbird's case
        self.objdir = objdir or self.origSrcDir
        self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)
        self.distDir = "%s/dist" % self.mozillaObjdir

        self.absSrcDir = "%s/%s" % (self.baseWorkDir,
                                    self.origSrcDir)
        self.absObjDir = '%s/%s' % (self.absSrcDir,
                                    self.objdir)
        self.absMozillaObjdir = '%s%s' % (self.absObjDir, self.mozillaDir)

        # Make sure MOZ_PKG_PRETTYNAMES is set so that our source package is
        # created in the expected place.
        self.env['MOZ_OBJDIR'] = WithProperties('%(basedir)s/' + self.absObjDir)
        self.env['MOZ_PKG_PRETTYNAMES'] = '1'
        if appVersion is None or version != appVersion:
            self.env['MOZ_PKG_VERSION'] = version
        self.env['MOZ_PKG_APPNAME'] = productName
        self.env['no_tooltool'] = "1"

        postUploadArgs = dict(
            product=productName,
            version=version,
            buildNumber=str(buildNumber),
            to_candidates=True,
            as_list=False,
            bucket_prefix=bucketPrefix,
        )
        if productName == 'fennec':
            postUploadArgs['product'] = 'mobile'
            postUploadArgs['nightly_dir'] = 'candidates'
        postUploadCmd = postUploadCmdPrefix(**postUploadArgs)
        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': stagingServer,
                          'UPLOAD_USER': stageUsername,
                          'UPLOAD_SSH_KEY': '~/.ssh/%s' % stageSshKey,
                          'UPLOAD_TO_TEMP': '1',
                          'POST_UPLOAD_CMD': postUploadCmd})

        # This will get us to the version we're building the release with
        self.addStep(self.makeHgtoolStep(
            workdir='.',
            wc=self.mozillaSrcDir,
            rev=self.releaseTag,
            use_properties=False,
        ))
        # ...And this will get us the tags so people can do things like
        # 'hg up -r FIREFOX_3_1b1_RELEASE' with the bundle
        self.addStep(ShellCommand(
                     name='hg_update_incl_tags',
                     command=['hg', 'up', '-C'],
                     workdir=self.mozillaSrcDir,
                     description=['update to', 'include tag revs'],
                     haltOnFailure=True
                     ))
        self.addStep(SetProperty(
                     name='hg_ident_revision',
                     command=['hg', 'identify', '-i'],
                     property='revision',
                     workdir=self.mozillaSrcDir,
                     haltOnFailure=True
                     ))
        self.addConfigSteps(workdir=self.mozillaSrcDir)
        self.addStep(MockCommand(
                     name='configure',
                     command=self.makeCmd + ['-f', 'client.mk', 'configure'],
                     workdir=self.mozillaSrcDir,
                     env=self.env,
                     description=['configure'],
                     mock=self.use_mock,
                     target=self.mock_target,
                     haltOnFailure=True
                     ))
        if self.enableSigning and self.signingServers:
            self.addGetTokenSteps()
        self.addStep(MockCommand(
            name='make_source-package',
            command=self.makeCmd + ['source-package', 'hg-bundle',
                     WithProperties('HG_BUNDLE_REVISION=%(revision)s')],
            workdir=self.absMozillaObjdir,
            env=self.env,
            description=['make source-package'],
            mock=self.use_mock,
            target=self.mock_target,
            haltOnFailure=True,
            timeout=60 * 60 * 2  # 2 hours
        ))
        self.addStep(RetryingMockCommand(
            name='upload_files',
            command=self.makeCmd + ['source-upload', 'UPLOAD_HG_BUNDLE=1'],
            workdir=self.absMozillaObjdir,
            env=uploadEnv,
            description=['upload files'],
            mock=self.use_mock,
            target=self.mock_target,
        ))

    def addConfigSteps(self, workdir='build'):
        self.addStep(ShellCommand(
                     name='cp_mozconfig',
                     command=['cp', self.mozconfig, '.mozconfig'],
                     description=['copying', 'mozconfig'],
                     descriptionDone=['copy', 'mozconfig'],
                     haltOnFailure=True,
                     workdir=workdir,
                     ))
        self.addStep(ShellCommand(
                     name='cat_mozconfig',
                     command=['cat', '.mozconfig'],
                     workdir=workdir
                     ))


class ReleaseUpdatesFactory(ReleaseFactory):

    def __init__(self, patcherConfig, verifyConfigs, appName, productName,
                 configRepoPath, version, appVersion, baseTag, buildNumber,
                 partialUpdates, ftpServer, bouncerServer,
                 hgSshKey, hgUsername, releaseChannel, localTestChannel, brandName=None,
                 buildSpace=2, triggerSchedulers=None, releaseNotesUrl=None,
                 python='python', promptWaitTime=None,
                 balrog_api_root=None, balrog_credentials_file=None,
                 balrog_username=None, mar_channel_ids=[], **kwargs):
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)

        self.patcherConfig = patcherConfig
        self.verifyConfigs = verifyConfigs
        self.appName = appName
        self.productName = productName
        self.version = version
        self.appVersion = appVersion
        self.baseTag = baseTag
        self.buildNumber = buildNumber
        self.partialUpdates = partialUpdates
        self.ftpServer = ftpServer
        self.bouncerServer = bouncerServer
        self.hgSshKey = hgSshKey
        self.hgUsername = hgUsername
        self.triggerSchedulers = triggerSchedulers
        self.python = python
        self.configRepoPath = configRepoPath
        self.promptWaitTime = promptWaitTime
        self.balrog_api_root = balrog_api_root
        self.balrog_credentials_file = balrog_credentials_file
        self.balrog_username = balrog_username
        self.testChannel = localTestChannel
        self.releaseChannel = releaseChannel
        self.mar_channel_ids = mar_channel_ids

        self.previousVersion = getPreviousVersion(self.version,
                                                  self.partialUpdates.keys())
        self.patcherConfigFile = 'tools/release/patcher-configs/%s' % patcherConfig
        self.shippedLocales = self.getShippedLocales(self.repository, baseTag,
                                                     appName)
        self.brandName = brandName or productName.capitalize()
        self.releaseNotesUrl = releaseNotesUrl

        self.bumpConfigs()
        if self.balrog_api_root:
            self.submitBalrogUpdates()
        self.trigger()

    def bumpConfigs(self):
        self.addStep(RetryingMockCommand(
                     name='get_shipped_locales',
                     command=['wget', '-O',
                              'shipped-locales', self.shippedLocales],
                     description=['get', 'shipped-locales'],
                     workdir='.',
                     env=self.env,
                     mock=self.use_mock,
                     target=self.mock_target,
                     haltOnFailure=True
                     ))
        bumpCommand = ['perl', 'tools/release/patcher-config-bump.pl',
                       '-p', self.productName, '-r', self.brandName,
                       '-v', self.version, '-a', self.appVersion,
                       '-o', self.previousVersion, '-b', str(self.buildNumber),
                       '-c', WithProperties(self.patcherConfigFile),
                       '-f', self.ftpServer,
                       '-d', self.bouncerServer, '-l', 'shipped-locales']
        for previousVersion in self.partialUpdates:
            bumpCommand.extend(['--partial-version', previousVersion])
        for platform in sorted(self.verifyConfigs.keys()):
            bumpCommand.extend(['--platform', platform])
        if self.promptWaitTime:
            bumpCommand.extend(['--prompt-wait-time', self.promptWaitTime])
        for c in self.mar_channel_ids:
            bumpCommand.extend(["--mar-channel-id", c])
        if self.releaseNotesUrl:
            rnurl = self.releaseNotesUrl
            if self.use_mock:
                rnurl = self.releaseNotesUrl.replace('%', '%%')
            bumpCommand.extend(['-n', rnurl])
        bump_env = self.env.copy()
        bump_env['PERL5LIB'] = 'tools/lib/perl'
        self.addStep(MockCommand(
                     name='bump',
                     command=bumpCommand,
                     description=['bump patcher config'],
                     env=bump_env,
                     workdir='.',
                     mock=self.use_mock,
                     target=self.mock_target,
                     haltOnFailure=True
                     ))

        # Bump the update verify configs
        pushRepo = self.getRepository(self.buildToolsRepoPath, push=True)
        sshKeyOption = self.getSshKeyOption(self.hgSshKey)
        tags = [release.info.getRuntimeTag(
            t) for t in release.info.getTags(self.baseTag, self.buildNumber)]
        releaseTag = release.info.getReleaseTag(self.baseTag)

        for platform, cfg in self.verifyConfigs.iteritems():
            command = [
                self.python, 'tools/scripts/updates/create-update-verify-configs.py',
                '-c', WithProperties(self.patcherConfigFile),
                '--platform', platform,
                '--output', 'tools/release/updates/' + cfg,
                '--release-config-file', WithProperties(
                    '%(release_config)s'),
                '-b', self.getRepository(self.configRepoPath),
                '--channel', self.testChannel,
                '-t', releaseTag]
            self.addStep(MockCommand(
                         name='bump_verify_configs',
                         command=command,
                         workdir='.',
                         env=self.env,
                         mock=self.use_mock,
                         target=self.mock_target,
                         description=['bump', self.verifyConfigs[platform]],
                         haltOnFailure=True,
                         ))
        self.addStep(TinderboxShellCommand(
                     name='diff_configs',
                     command=['hg', 'diff'],
                     workdir='tools',
                     ignoreCodes=[0, 1]
                     ))
        self.addStep(MockCommand(
                     name='commit_configs',
                     command=['hg', 'commit', '-u', self.hgUsername, '-m',
                              'Automated configuration bump: update configs ' +
                              'for %s %s build %s on %s' % (self.brandName, self.version,
                                                            self.buildNumber,
                                                            self.releaseChannel)
                              ],
                     description=['commit configs'],
                     workdir='tools',
                     env=self.env,
                     mock=self.use_mock,
                     target=self.mock_target,
                     haltOnFailure=True
                     ))
        self.addStep(SetProperty(
            command=['hg', 'identify', '-i'],
            property='configRevision',
            workdir='tools',
            haltOnFailure=True
        ))
        self.addStep(ShellCommand(
            name='tag_configs',
            command=['hg', 'tag', '-u', self.hgUsername, '-f',
                     '-r', WithProperties('%(configRevision)s')] + tags,
            description=['tag configs'],
            workdir='tools',
            haltOnFailure=True
        ))
        self.addStep(RetryingMockCommand(
                     name='push_configs',
                     command=['hg', 'push', '-e',
                              'ssh -l %s %s' % (self.hgUsername, sshKeyOption),
                              '-f', pushRepo],
                     description=['push configs'],
                     workdir='tools',
                     mock=self.use_mock,
                     target=self.mock_target,
                     env=self.env,
                     haltOnFailure=True
                     ))

    def submitBalrogUpdates(self):
        self.addStep(JSONPropertiesDownload(
            name='download_balrog_props',
            slavedest='buildprops_balrog.json',
            workdir='.',
            flunkOnFailure=True,
        ))
        credentials_file = os.path.join(os.getcwd(),
                                        self.balrog_credentials_file)
        target_file_name = os.path.basename(credentials_file)
        self.addStep(FileDownload(
            mastersrc=credentials_file,
            slavedest=target_file_name,
            workdir='.',
            flunkOnFailure=False,
        ))
        cmd = [
            self.python,
            WithProperties('%(toolsdir)s/scripts/updates/balrog-release-pusher.py'),
            '--build-properties', 'buildprops_balrog.json',
            '--api-root', self.balrog_api_root,
            '--buildbot-configs', self.getRepository(self.configRepoPath),
            '--release-config', WithProperties('%(release_config)s'),
            '--credentials-file', target_file_name,
            '--username', self.balrog_username,
            '--release-channel', self.releaseChannel,
        ]
        self.addStep(RetryingShellCommand(
            name='submit_balrog_updates',
            command=cmd,
            workdir='.',
            flunkOnFailure=True,
        ))

    def trigger(self):
        if self.triggerSchedulers:
            self.addStep(Trigger(
                         schedulerNames=self.triggerSchedulers,
                         waitForFinish=False
                         ))


class ReleaseFinalVerification(ReleaseFactory):
    def __init__(self, verifyConfigs, platforms=None, **kwargs):
        # MozillaBuildFactory needs the 'repoPath' argument, but we don't
        if 'repoPath' not in kwargs:
            kwargs['repoPath'] = 'nothing'
        ReleaseFactory.__init__(self, **kwargs)
        verifyCommand = ['bash', 'final-verification.sh']
        platforms = platforms or sorted(verifyConfigs.keys())
        for platform in platforms:
            verifyCommand.append(verifyConfigs[platform])
        self.addStep(ShellCommand(
                     name='final_verification',
                     command=verifyCommand,
                     description=['final-verification.sh'],
                     workdir='tools/release',
                     timeout=2400,
                     ))


def parse_sendchange_files(build, include_substr='', exclude_substrs=[]):
    '''Given a build object, figure out which files have the include_substr
    in them, then exclude files that have one of the exclude_substrs. This
    function uses substring pattern matching instead of regular expressions
    as it meets the need without incurring as much overhead.'''
    potential_files = []
    for file in build.source.changes[-1].files:
        if include_substr in file and file not in potential_files:
            potential_files.append(file)
    assert len(potential_files) > 0, 'sendchange missing this archive type'
    for substring in exclude_substrs:
        for f in potential_files[:]:
            if substring in f:
                potential_files.remove(f)
    assert len(potential_files) == 1, 'Ambiguous testing sendchange!'
    return potential_files[0]


class MozillaTestFactory(MozillaBuildFactory):
    def __init__(self, platform, productName='firefox',
                 downloadSymbols=True, downloadSymbolsOnDemand=True,
                 downloadTests=False, posixBinarySuffix='-bin',
                 resetHwClock=False, macResSubdir='Resources', **kwargs):
        # Note: the posixBinarySuffix is needed because some products (firefox)
        # use 'firefox-bin' and some (fennec) use 'fennec' for the name of the
        # actual application binary.  This is only applicable to posix-like
        # systems.  Windows always uses productName.exe (firefox.exe and
        # fennec.exe)
        self.platform = platform.split('-')[0]
        self.productName = productName
        if not posixBinarySuffix:
            # all forms of no should result in empty string
            self.posixBinarySuffix = ''
        else:
            self.posixBinarySuffix = posixBinarySuffix
        self.macResSubdir = macResSubdir
        self.downloadSymbols = downloadSymbols
        self.downloadSymbolsOnDemand = downloadSymbolsOnDemand
        self.downloadTests = downloadTests
        self.resetHwClock = resetHwClock

        assert self.platform in getSupportedPlatforms()

        MozillaBuildFactory.__init__(self, **kwargs)

        self.ignoreCerts = False

        self.addCleanupSteps()
        self.addPrepareBuildSteps()
        if self.downloadSymbols or self.downloadSymbolsOnDemand:
            self.addPrepareSymbolsSteps()
        if self.downloadTests:
            self.addPrepareTestsSteps()
        self.addIdentifySteps()
        self.addSetupSteps()
        self.addRunTestSteps()
        self.addTearDownSteps()

    def addInitialSteps(self):
        def get_revision(build):
            try:
                revision = build.source.changes[-1].revision
                return revision
            except:
                return "not-set"
        self.addStep(SetBuildProperty(
                     property_name="revision",
                     value=get_revision,
                     ))

        def get_who(build):
            try:
                revision = build.source.changes[-1].who
                return revision
            except:
                return "not-set"

        self.addStep(SetBuildProperty(
                     property_name="who",
                     value=get_who,
                     ))

        MozillaBuildFactory.addInitialSteps(self)

    def addCleanupSteps(self):
        '''Clean up the relevant places before starting a build'''
        # On windows, we should try using cmd's attrib and native rmdir
        self.addStep(ShellCommand(
            name='rm_builddir',
            command=['rm', '-rf', 'build'],
            workdir='.'
        ))

    def addPrepareBuildSteps(self):
        '''This function understands how to prepare a build for having tests run
        against it.  It downloads, unpacks then sets important properties for use
        during testing'''
        def get_build_url(build):
            '''Make sure that there is at least one build in the file list'''
            assert len(build.source.changes[-1]
                       .files) > 0, 'Unittest sendchange has no files'
            return parse_sendchange_files(
                build, exclude_substrs=['.crashreporter-symbols.',
                                        '.tests.'])
        self.addStep(DownloadFile(
            url_fn=get_build_url,
            filename_property='build_filename',
            url_property='build_url',
            haltOnFailure=True,
            ignore_certs=self.ignoreCerts,
            name='download_build',
        ))
        self.addStep(UnpackFile(
            filename=WithProperties('%(build_filename)s'),
            scripts_dir='../tools/buildfarm/utils',
            haltOnFailure=True,
            name='unpack_build',
        ))
        # Find the application binary!
        if self.platform.startswith('macosx'):
            self.addStep(FindFile(
                filename="%s%s" % (self.productName, self.posixBinarySuffix),
                directory=".",
                max_depth=4,
                property_name="exepath",
                name="find_executable",
            ))
        elif self.platform.startswith('win'):
            self.addStep(SetBuildProperty(
                         property_name="exepath",
                         value="%s/%s.exe" % (
                             self.productName, self.productName),
                         ))
        else:
            self.addStep(SetBuildProperty(
                         property_name="exepath",
                         value="%s/%s%s" % (self.productName, self.productName,
                                            self.posixBinarySuffix),
                         ))

        def get_exedir(build):
            return os.path.dirname(build.getProperty('exepath'))
        self.addStep(SetBuildProperty(
                     property_name="exedir",
                     value=get_exedir,
                     ))

        # OSX 10.9.5+ requires putting extensions and plugins into
        # Contents/Resources, this property sets up that directory.
        def get_xredir(build):
            if self.platform.startswith("macosx"):
                contentsdir = os.path.dirname(get_exedir(build))
                return os.path.join(contentsdir, self.macResSubdir)
            else:
                return get_exedir(build)
        self.addStep(SetBuildProperty(
                     property_name="xredir",
                     value=get_xredir,
                     ))

        # Need to override toolsdir as set by MozillaBuildFactory because
        # we need Windows-style paths for the stack walker.
        if self.platform.startswith('win'):
            self.addStep(SetProperty(
                         command=['bash', '-c', 'pwd -W'],
                         property='toolsdir',
                         workdir='tools'
                         ))

    def addPrepareSymbolsSteps(self):
        '''This function knows how to setup the symbols for a build to be useful'''
        def get_symbols_url(build):
            '''If there are two files, we assume that the second file is the tests tarball
            and use the same location as the build, with the build's file extension replaced
            with .crashreporter-symbols.zip.  If there are three or more files then we figure
            out which is the real file'''
            if len(build.source.changes[-1].files) < 3:
                build_url = build.getProperty('build_url')
                for suffix in ('.tar.bz2', '.zip', '.dmg', '.exe', '.apk'):
                    if build_url.endswith(suffix):
                        return build_url[:-len(suffix)] + '.crashreporter-symbols.zip'
            else:
                return parse_sendchange_files(build, include_substr='.crashreporter-symbols.')

        if self.downloadSymbols:
            self.addStep(DownloadFile(
                url_fn=get_symbols_url,
                filename_property='symbols_filename',
                url_property='symbols_url',
                name='download_symbols',
                ignore_certs=self.ignoreCerts,
                workdir='build/symbols'
            ))
            self.addStep(UnpackFile(
                filename=WithProperties('%(symbols_filename)s'),
                name='unpack_symbols',
                workdir='build/symbols'
            ))
        elif self.downloadSymbolsOnDemand:
            self.addStep(SetBuildProperty(
                property_name='symbols_url',
                value=get_symbols_url,
            ))

    def addPrepareTestsSteps(self):
        def get_tests_url(build):
            '''If there is only one file, we assume that the tests package is at
            the same location with the file extension of the browser replaced with
            .tests.tar.bz2, otherwise we try to find the explicit file'''
            if len(build.source.changes[-1].files) < 2:
                build_url = build.getProperty('build_url')
                for suffix in ('.tar.bz2', '.zip', '.dmg', '.exe'):
                    if build_url.endswith(suffix):
                        return build_url[:-len(suffix)] + '.tests.tar.bz2'
            else:
                return parse_sendchange_files(build, include_substr='.tests.')
        self.addStep(DownloadFile(
            url_fn=get_tests_url,
            filename_property='tests_filename',
            url_property='tests_url',
            haltOnFailure=True,
            ignore_certs=self.ignoreCerts,
            name='download tests',
        ))

    def addIdentifySteps(self):
        '''This function knows how to figure out which build this actually is
        and display it in a useful way'''
        # Figure out build ID and TinderboxPrint revisions
        def get_build_info(rc, stdout, stderr):
            retval = {}
            stdout = "\n".join([stdout, stderr])
            m = re.search("^buildid: (\w+)", stdout, re.M)
            if m:
                retval['buildid'] = m.group(1)
            return retval
        self.addStep(SetProperty(
                     command=['python', WithProperties('%(toolsdir)s/buildfarm/utils/printbuildrev.py'),
                              WithProperties('%(xredir)s')],
                     workdir='build',
                     extract_fn=get_build_info,
                     name='get build info',
                     ))

    def addSetupSteps(self):
        '''This stub is for implementing classes to do harness specific setup'''
        pass

    def addRunTestSteps(self):
        '''This stub is for implementing classes to do the actual test runs'''
        pass

    def addTearDownSteps(self):
        self.addCleanupSteps()
        if 'macosx64' in self.platform:
            # This will fail cleanly and silently on 10.6
            # but is important on 10.7
            self.addStep(ShellCommand(
                name="clear_saved_state",
                flunkOnFailure=False,
                warnOnFailure=False,
                haltOnFailure=False,
                workdir='/Users/cltbld',
                command=['bash', '-c',
                         'rm -rf Library/Saved\ Application\ State/*.savedState']
            ))
        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            # This step is to deal with minis running linux that don't reboot properly
            # see bug561442
            if self.resetHwClock and 'linux' in self.platform:
                self.addStep(ShellCommand(
                    name='set_time',
                    description=['set', 'time'],
                    alwaysRun=True,
                    command=['bash', '-c',
                             'sudo hwclock --set --date="$(date +%m/%d/%y\ %H:%M:%S)"'],
                ))
            self.addPeriodicRebootSteps()


def rc_eval_func(exit_statuses):
    def eval_func(cmd, step):
        rc = cmd.rc
        # Temporarily set the rc to 0 so that regex_log_evaluator won't say a
        # command has failed because of non-zero exit code.  We're handing exit
        # codes here.
        try:
            cmd.rc = 0
            regex_status = regex_log_evaluator(cmd, step, global_errors)
        finally:
            cmd.rc = rc

        if cmd.rc in exit_statuses:
            rc_status = exit_statuses[cmd.rc]
        # Use None to specify a default value if you don't want the
        # normal 0 -> SUCCESS, != 0 -> FAILURE
        elif None in exit_statuses:
            rc_status = exit_statuses[None]
        elif cmd.rc == 0:
            rc_status = SUCCESS
        else:
            rc_status = FAILURE

        return worst_status(regex_status, rc_status)
    return eval_func


def extractProperties(rv, stdout, stderr):
    props = {}
    stdout = stdout.strip()
    for l in filter(None, stdout.split('\n')):
        e = filter(None, l.split(':', 1))
        if len(e) == 2:
            props[e[0]] = e[1].strip()
    return props


def extractJSONProperties(rv, stdout, stderr):
    props = {}
    try:
        stdout = stdout.strip()
        props.update(json.loads(stdout))
    finally:
        return props


class ScriptFactory(RequestSortingBuildFactory, TooltoolMixin):

    def __init__(self, scriptRepo, scriptName, script_repo_manifest=None,
                 cwd=None, interpreter=None,
                 extra_data=None, extra_args=None, use_credentials_file=False,
                 script_timeout=1200, script_maxtime=None, log_eval_func=None,
                 reboot_command=None, hg_bin='hg', platform=None,
                 use_mock=False, mock_target=None,
                 mock_packages=None, mock_copyin_files=None,
                 triggered_schedulers=None, env={}, copy_properties=None,
                 properties_file='buildprops.json', script_repo_cache=None,
                 tools_repo_cache=None, tooltool_manifest_src=None,
                 tooltool_bootstrap="setup.sh", tooltool_url_list=None,
                 tooltool_script=None, relengapi_archiver_repo_path=None,
                 relengapi_archiver_release_tag=None, relengapi_archiver_rev=None):
        BuildFactory.__init__(self)
        self.script_timeout = script_timeout
        self.log_eval_func = log_eval_func
        self.script_maxtime = script_maxtime
        self.reboot_command = reboot_command
        self.platform = platform
        self.use_mock = use_mock
        self.mock_target = mock_target
        self.mock_packages = mock_packages
        self.mock_copyin_files = mock_copyin_files
        self.get_basedir_cmd = ['bash', '-c', 'pwd']
        self.triggered_schedulers = triggered_schedulers
        self.env = env.copy()
        self.use_credentials_file = use_credentials_file
        self.copy_properties = copy_properties or []
        self.script_repo_cache = script_repo_cache
        self.tools_repo_cache = tools_repo_cache
        self.tooltool_manifest_src = tooltool_manifest_src
        self.tooltool_url_list = tooltool_url_list or []
        self.tooltool_script = tooltool_script or ['/tools/tooltool.py']
        self.tooltool_bootstrap = tooltool_bootstrap

        assert len(self.tooltool_url_list) <= 1, "multiple urls not currently supported by tooltool"

        if platform and 'win' in platform:
            self.get_basedir_cmd = ['cd']

        self.addStep(SetBuildProperty(
            property_name='master',
            value=lambda b: b.builder.botmaster.parent.buildbotURL
        ))
        self.env['PROPERTIES_FILE'] = WithProperties(
            '%(basedir)s/' + properties_file)
        self.addStep(JSONPropertiesDownload(
            name="download_props",
            slavedest=properties_file,
            workdir="."
        ))
        if extra_data:
            self.addStep(JSONStringDownload(
                extra_data,
                name="download_extra",
                slavedest="data.json",
                workdir="."
            ))
            self.env['EXTRA_DATA'] = WithProperties('%(basedir)s/data.json')

        if relengapi_archiver_repo_path:
            if relengapi_archiver_release_tag:
                archiver_revision = "--tag %s " % relengapi_archiver_release_tag
                script_repo_revision = relengapi_archiver_release_tag
            else:
                archiver_revision = "--rev %s " % (relengapi_archiver_rev or '%(revision)s',)
                script_repo_revision = "%s" % (relengapi_archiver_rev or '%(revision)s',)
            if self.script_repo_cache:
                assert self.tools_repo_cache
                archiver_client_path = \
                    os.path.join(self.tools_repo_cache,
                                 'buildfarm',
                                 'utils',
                                 'archiver_client.py')
            else:
                self.addStep(ShellCommand(
                    command=['bash', '-c',
                             'wget -Oarchiver_client.py ' +
                             '--no-check-certificate --tries=10 --waitretry=3 ' +
                             'https://hg.mozilla.org/build/tools/raw-file/default/buildfarm/utils/archiver_client.py'],
                    haltOnFailure=True,
                    workdir=".",
                ))
                archiver_client_path = 'archiver_client.py'

            self.addStep(ShellCommand(
                name="clobber_scripts",
                command=['rm', '-rf', 'scripts', 'properties'],
                workdir=".",
                haltOnFailure=True,
                log_eval_func=rc_eval_func({0: SUCCESS, None: RETRY}),
            ))
            self.addStep(ShellCommand(
                name="download_and_extract_scripts_archive",
                command=['bash', '-c',
                         WithProperties(
                             'python %s ' % archiver_client_path +
                             'mozharness ' +
                             '--repo %s ' % relengapi_archiver_repo_path +
                             archiver_revision +
                             '--destination scripts ' +
                             '--debug')],
                log_eval_func=rc_eval_func({0: SUCCESS, None: EXCEPTION}),
                haltOnFailure=True,
                workdir=".",
            ))
            if scriptName.startswith('/'):
                script_path = scriptName
            else:
                script_path = 'scripts/%s' % scriptName
            self.addStep(SetBuildProperty(
                name='get_script_repo_revision',
                property_name='script_repo_revision',
                value=lambda b: b.getProperties().render(WithProperties(script_repo_revision)),
                haltOnFailure=False,
            ))
        elif self.script_repo_cache:
            # This code path is no longer used
            assert False, 'script_repo_cache is not used any more on its own'
        else:
            # fall back to legacy clobbering + cloning script repo
            if script_repo_manifest:
                assert False, 'legacy script_repo_manifest unsupported now'

            self.addStep(ShellCommand(
                name="clobber_scripts",
                command=['rm', '-rf', 'scripts', 'properties'],
                workdir=".",
                haltOnFailure=True,
                log_eval_func=rc_eval_func({0: SUCCESS, None: RETRY}),
            ))
            self.addStep(MercurialCloneCommand(
                name="clone_scripts",
                command=[hg_bin, 'clone', scriptRepo, 'scripts'],
                workdir=".",
                haltOnFailure=True,
                retry=False,
                log_eval_func=rc_eval_func({0: SUCCESS, None: RETRY}),
            ))
            self.addStep(ShellCommand(
                name="update_scripts",
                command=[hg_bin, 'update', '-C', '-r',
                         WithProperties('%(script_repo_revision:-default)s')],
                haltOnFailure=True,
                workdir='scripts'
            ))
            self.addStep(SetProperty(
                name='get_script_repo_revision',
                property='script_repo_revision',
                command=[hg_bin, 'id', '-i'],
                workdir='scripts',
                haltOnFailure=False,
            ))
            if scriptName[0] == '/':
                script_path = scriptName
            else:
                script_path = 'scripts/%s' % scriptName


        if interpreter:
            if isinstance(interpreter, (tuple, list)):
                self.cmd = list(interpreter) + [script_path]
            else:
                self.cmd = [interpreter, script_path]
        else:
            self.cmd = [script_path]

        if extra_args:
            self.cmd.extend(extra_args)


        if use_credentials_file:
            self.addStep(FileDownload(
                mastersrc=os.path.join(os.getcwd(), 'BuildSlaves.py'),
                slavedest='oauth.txt',
                workdir='.',
                flunkOnFailure=False,
            ))
        self.runScript()
        self.addCleanupSteps()
        self.reboot()

    def addCleanupSteps(self):
        # remove oauth.txt file, we don't wanna to leave keys lying around
        if self.use_credentials_file:
            self.addStep(ShellCommand(
                name='rm_oauth_txt',
                command=['rm', '-f', 'oauth.txt'],
                workdir='.',
                alwaysRun=True
            ))

    def preRunScript(self):
        if self.use_mock:
            self.addStep(MockReset(
                target=self.mock_target,
            ))
            self.addStep(MockInit(
                target=self.mock_target,
            ))
            if self.mock_copyin_files:
                for source, target in self.mock_copyin_files:
                    self.addStep(ShellCommand(
                        name='mock_copyin_%s' % source.replace('/', '_'),
                        command=['mock_mozilla', '-r', self.mock_target,
                                 '--copyin', source, target],
                        haltOnFailure=True,
                    ))
                    self.addStep(MockCommand(
                        name='mock_chown_%s' % target.replace('/', '_'),
                        command='chown -R mock_mozilla %s' % target,
                        target=self.mock_target,
                        mock=True,
                        workdir='/',
                        mock_args=[],
                        mock_workdir_prefix=None,
                    ))

            self.addStep(MockInstall(
                target=self.mock_target,
                packages=self.mock_packages,
                timeout=2700,
            ))

        if self.tooltool_manifest_src:
            self.addStep(SetProperty(
                name='set_toolsdir',
                command=['bash', '-c', 'pwd'],
                property='toolsdir',
                workdir='scripts',
            ))
            self.addTooltoolStep(workdir='build')

    def runScript(self, env=None):
        if not env:
            env = self.env
        self.preRunScript()
        self.addStep(MockCommand(
            name="run_script",
            command=self.cmd,
            env=env,
            timeout=self.script_timeout,
            maxTime=self.script_maxtime,
            log_eval_func=self.log_eval_func,
            workdir=".",
            haltOnFailure=True,
            warnOnWarnings=True,
            mock=self.use_mock,
            target=self.mock_target,
        ))

        cmd = ['bash', '-c', 'for file in `ls -1`; do cat $file; done']
        if self.platform and 'win' in self.platform:
            # note: prefixing 'type' command with '@' to suppress extraneous
            # output
            cmd = ['cmd', '/C', 'for', '%f', 'in', '(*)', 'do', '@type', '%f']

        self.addStep(SetProperty(
            name='set_script_properties',
            command=cmd,
            workdir='properties',
            extract_fn=extractProperties,
            alwaysRun=True,
            warnOnFailure=False,
            flunkOnFailure=False,
        ))

        if self.triggered_schedulers:
            for triggered_scheduler in self.triggered_schedulers:
                self.addStep(Trigger(
                    schedulerNames=[triggered_scheduler],
                    copy_properties=self.copy_properties,
                    waitForFinish=False)
                )

    def reboot(self):
        def do_disconnect(cmd):
            try:
                if 'SCHEDULED REBOOT' in cmd.logs['stdio'].getText():
                    return True
            except:
                pass
            return False
        if self.reboot_command:
            self.addStep(DisconnectStep(
                name='reboot',
                flunkOnFailure=False,
                warnOnFailure=False,
                alwaysRun=True,
                workdir='.',
                description="reboot",
                command=self.reboot_command,
                force_disconnect=do_disconnect,
                env=self.env,
            ))


class SigningScriptFactory(ScriptFactory):

    def __init__(self, signingServers, enableSigning=True, **kwargs):
        self.signingServers = signingServers
        self.enableSigning = enableSigning
        ScriptFactory.__init__(self, **kwargs)

    def runScript(self):

        signing_env = None
        if self.enableSigning:
            token = "token"
            nonce = "nonce"
            self.addStep(ShellCommand(
                command=['rm', '-f', nonce],
                workdir='.',
                name='rm_nonce',
                description=['remove', 'old', 'nonce'],
            ))
            self.addStep(SigningServerAuthenication(
                servers=self.signingServers,
                server_cert=SIGNING_SERVER_CERT,
                slavedest=token,
                workdir='.',
                name='download_token',
            ))
            # toolsdir, basedir
            if self.tools_repo_cache:
                self.addStep(SetProperty(
                    name='set_toolsdir',
                    command=['bash', '-c', 'pwd'],
                    property='toolsdir',
                    workdir=self.tools_repo_cache
                ))
            else:
                self.addStep(SetProperty(
                    name='set_toolsdir',
                    command=self.get_basedir_cmd,
                    property='toolsdir',
                    workdir='scripts',
                ))
            signing_env = self.env.copy()
            signing_env['MOZ_SIGN_CMD'] = WithProperties(get_signing_cmd(
                self.signingServers, self.env.get('PYTHON26')))
            signing_env['MOZ_SIGNING_SERVERS'] = ",".join(
                "%s:%s" % (":".join(s[3]), s[0]) for s in self.signingServers)

        ScriptFactory.runScript(self, env=signing_env)
