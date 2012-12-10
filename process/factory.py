from __future__ import absolute_import

import os.path, re
import urllib
import random
from distutils.version import LooseVersion

from twisted.python import log

from buildbot.process.buildstep import regex_log_evaluator
from buildbot.process.factory import BuildFactory
from buildbot.steps.shell import WithProperties
from buildbot.steps.transfer import FileDownload, JSONPropertiesDownload, JSONStringDownload
from buildbot.steps.dummy import Dummy
from buildbot import locks
from buildbot.status.builder import worst_status

import buildbotcustom.common
import buildbotcustom.status.errors
import buildbotcustom.steps.base
import buildbotcustom.steps.misc
import buildbotcustom.steps.release
import buildbotcustom.steps.source
import buildbotcustom.steps.test
import buildbotcustom.steps.updates
import buildbotcustom.steps.talos
import buildbotcustom.steps.unittest
import buildbotcustom.steps.signing
import buildbotcustom.steps.mock
import buildbotcustom.env
import buildbotcustom.misc_scheduler
import build.paths
import release.info
import release.paths
reload(buildbotcustom.common)
reload(buildbotcustom.status.errors)
reload(buildbotcustom.steps.base)
reload(buildbotcustom.steps.misc)
reload(buildbotcustom.steps.release)
reload(buildbotcustom.steps.source)
reload(buildbotcustom.steps.test)
reload(buildbotcustom.steps.updates)
reload(buildbotcustom.steps.talos)
reload(buildbotcustom.steps.unittest)
reload(buildbotcustom.steps.signing)
reload(buildbotcustom.steps.mock)
reload(buildbotcustom.env)
reload(build.paths)
reload(release.info)
reload(release.paths)

from buildbotcustom.status.errors import purge_error, global_errors, \
  upload_errors, talos_hgweb_errors, tegra_errors
from buildbotcustom.steps.base import ShellCommand, SetProperty, Mercurial, \
  Trigger, RetryingShellCommand
from buildbotcustom.steps.misc import TinderboxShellCommand, SendChangeStep, \
  MozillaClobberer, FindFile, DownloadFile, UnpackFile, \
  SetBuildProperty, DisconnectStep, OutputStep, \
  RepackPartners, UnpackTest, FunctionalStep, setBuildIDProps
from buildbotcustom.steps.release import SnippetComparison
from buildbotcustom.steps.source import MercurialCloneCommand
from buildbotcustom.steps.test import GraphServerPost
from buildbotcustom.steps.updates import CreateCompleteUpdateSnippet, \
  CreatePartialUpdateSnippet
from buildbotcustom.steps.signing import SigningServerAuthenication
from buildbotcustom.env import MozillaEnvironments
from buildbotcustom.common import getSupportedPlatforms, getPlatformFtpDir, \
  genBuildID, reallyShort
from buildbotcustom.steps.mock import MockReset, MockInit, MockCommand, MockInstall, \
  MockMozillaCheck, MockProperty, RetryingMockProperty, RetryingMockCommand, \
  MockAliveTest, MockCompareLeakLogs

import buildbotcustom.steps.unittest as unittest_steps

import buildbotcustom.steps.talos as talos_steps
from buildbot.status.builder import SUCCESS, FAILURE, RETRY

from release.paths import makeCandidatesDir

# limit the number of clones of the try repository so that we don't kill
# dm-vcview04 if the master is restarted, or there is a large number of pushes
hg_try_lock = locks.MasterLock("hg_try_lock", maxCount=20)

hg_l10n_lock = locks.MasterLock("hg_l10n_lock", maxCount=20)

# Limit ourselves to uploading 4 things at a time
upload_lock = locks.MasterLock("upload_lock", maxCount=4)

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

def makeDummyBuilder(name, slaves, category=None, delay=0, triggers=None, properties=None):
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
        to_shadow=False,
        to_candidates=False,
        to_mobile_candidates=False,
        nightly_dir=None,
        as_list=True,
        signed=False,
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
        cmd.extend(["-p", product])
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
    if to_tinderbox_builds:
        cmd.append('--release-to-tinderbox-builds')
    if to_try:
        cmd.append('--release-to-try-builds')
    if to_latest:
        cmd.append("--release-to-latest")
    if to_dated:
        cmd.append("--release-to-dated")
    if to_shadow:
        cmd.append("--release-to-shadow-central-builds")
    if to_candidates:
        cmd.append("--release-to-candidates-dir")
    if to_mobile_candidates:
        cmd.append("--release-to-mobile-candidates-dir")
    if nightly_dir:
        cmd.append("--nightly-dir=%s" % nightly_dir)
    if signed:
        cmd.append("--signed")

    if as_list:
        return cmd
    else:
        # Remove WithProperties instances and replace them with their fmtstring
        for i,a in enumerate(cmd):
            if isinstance(a, WithProperties):
                cmd[i] = a.fmtstring
        return WithProperties(' '.join(cmd))

def parse_make_upload(rc, stdout, stderr):
    ''' This function takes the output and return code from running
    the upload make target and returns a dictionary of important
    file urls.'''
    retval = {}
    for m in re.findall("^(https?://.*?\.(?:tar\.bz2|dmg|zip|apk|rpm|mar|tar\.gz))$",
                        "\n".join([stdout, stderr]), re.M):
        if 'devel' in m and m.endswith('.rpm'):
            retval['develRpmUrl'] = m
        elif 'tests' in m and m.endswith('.rpm'):
            retval['testsRpmUrl'] = m
        elif m.endswith('.rpm'):
            retval['packageRpmUrl'] = m
        elif m.endswith("crashreporter-symbols.zip"):
            retval['symbolsUrl'] = m
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
    for ss, user, passwd in signingServers:
        cmd.extend(['-H', ss])
    return ' '.join(cmd)

def getPlatformMinidumpPath(platform):
    platform_minidump_path = {
        'linux': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
        'linuxqt': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
        'linux32_gecko': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
        'linux64': WithProperties('%(toolsdir:-)s/breakpad/linux64/minidump_stackwalk'),
        'win32': WithProperties('%(toolsdir:-)s/breakpad/win32/minidump_stackwalk.exe'),
        'win32_gecko': WithProperties('%(toolsdir:-)s/breakpad/win32/minidump_stackwalk.exe'),
        'win64': WithProperties('%(toolsdir:-)s/breakpad/win64/minidump_stackwalk.exe'),
        'macosx': WithProperties('%(toolsdir:-)s/breakpad/osx/minidump_stackwalk'),
        'macosx64': WithProperties('%(toolsdir:-)s/breakpad/osx64/minidump_stackwalk'),
        'macosx64_gecko': WithProperties('%(toolsdir:-)s/breakpad/osx/minidump_stackwalk'),
        # Android uses OSX *and* Linux because the Foopies are on both.
        'android': WithProperties('/builds/minidump_stackwalk'),
        'android-noion': WithProperties('/builds/minidump_stackwalk'),
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

            props = [request.properties] + [c.properties for c in request.source.changes]

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
                    name='mock_copyin_%s' % source.replace('/','_'),
                    command=['mock_mozilla', '-r', self.mock_target,
                             '--copyin', source, target],
                    haltOnFailure=True,
                ))
                self.addStep(MockCommand(
                    name='mock_chown_%s' % target.replace('/','_'),
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
            command=WithProperties("mkdir -p %(basedir)s" + "/%s" % self.baseWorkDir),
            target=self.mock_target,
            mock=True,
            workdir='/',
            mock_workdir_prefix=None,
        ))
        if self.use_mock and self.mock_packages:
            self.addStep(MockInstall(
                target=self.mock_target,
                packages=self.mock_packages,
            ))


class MozillaBuildFactory(RequestSortingBuildFactory, MockMixin):
    ignore_dirs = [ 'info', 'rel-*']

    def __init__(self, hgHost, repoPath, buildToolsRepoPath, buildSpace=0,
                 clobberURL=None, clobberBranch=None, clobberTime=None,
                 buildsBeforeReboot=None, branchName=None, baseWorkDir='build',
                 hashType='sha512', baseMirrorUrls=None, baseBundleUrls=None,
                 signingServers=None, enableSigning=True, env={},
                 balrog_api_root=None, balrog_credentials_file=None,
                 use_mock=False, mock_target=None,
                 mock_packages=None, mock_copyin_files=None,
                 makeCmd=['make'], **kwargs):
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
        self.use_mock = use_mock
        self.mock_target = mock_target
        self.mock_packages = mock_packages
        self.mock_copyin_files = mock_copyin_files
        self.makeCmd = makeCmd

        self.repository = self.getRepository(repoPath)
        if branchName:
          self.branchName = branchName
        else:
          self.branchName = self.getRepoName(self.repository)
        if not clobberBranch:
            self.clobberBranch = self.branchName
        else:
            self.clobberBranch = clobberBranch

        if self.signingServers and self.enableSigning:
            self.signing_command = get_signing_cmd(
                self.signingServers, self.env.get('PYTHON26'))
            self.env['MOZ_SIGN_CMD'] = WithProperties(self.signing_command)

        self.addInitialSteps()

    def addInitialSteps(self):
        self.addStep(SetProperty(
            name='set_basedir',
            command=['bash', '-c', 'pwd'],
            property='basedir',
            workdir='.',
        ))
        self.addStep(SetProperty(
            name='set_hashType',
            command=['echo', self.hashType],
            property='hashType',
            workdir='.',
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
         workdir='.',
         retry=False
        ))
        self.addStep(SetProperty(
            name='set_toolsdir',
            command=['bash', '-c', 'pwd'],
            property='toolsdir',
            workdir='tools',
        ))

        if self.clobberURL is not None:
            self.addStep(MozillaClobberer(
             name='checking_clobber_times',
             branch=self.clobberBranch,
             clobber_url=self.clobberURL,
             clobberer_path=WithProperties('%(builddir)s/tools/clobberer/clobberer.py'),
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
            command.extend(["..","/mock/users/cltbld/home/cltbld/build"])

            def parse_purge_builds(rc, stdout, stderr):
                properties = {}
                for stream in (stdout, stderr):
                    m = re.search('unable to free (?P<size>[.\d]+) (?P<unit>\w+) ', stream, re.M)
                    if m:
                        properties['purge_target'] = '%s%s' % (m.group('size'), m.group('unit'))
                    m = None
                    m = re.search('space only (?P<size>[.\d]+) (?P<unit>\w+)', stream, re.M)
                    if m:
                        properties['purge_actual'] = '%s%s' % (m.group('size'), m.group('unit'))
                    m = None
                    m = re.search('(?P<size>[.\d]+) (?P<unit>\w+) of space available', stream, re.M)
                    if m:
                        properties['purge_actual'] = '%s%s' % (m.group('size'), m.group('unit'))
                if not properties.has_key('purge_target'):
                    properties['purge_target'] = '%sGB' % str(self.buildSpace)
                return properties

            self.addStep(SetProperty(
             name='clean_old_builds',
             command=command,
             description=['cleaning', 'old', 'builds'],
             descriptionDone=['clean', 'old', 'builds'],
             haltOnFailure=True,
             workdir='.',
             timeout=3600, # One hour, because Windows is slow
             extract_fn=parse_purge_builds,
             log_eval_func=lambda c,s: regex_log_evaluator(c, s, purge_error),
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
         command=['python', 'tools/buildfarm/maintenance/count_and_reboot.py',
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
        for prefix in ('http://', 'ssh://'):
            if repoPath.startswith(prefix):
                return repoPath
        if repoPath.startswith('/'):
            repoPath = repoPath.lstrip('/')
        if not hgHost:
            hgHost = self.hgHost
        proto = 'ssh' if push else 'http'
        return '%s://%s/%s' % (proto, hgHost, repoPath)

    def getPackageFilename(self, platform, platform_variation):
        if 'android-armv6' in self.complete_platform:
            packageFilename = '*arm-armv6.apk'
        elif 'android' in self.complete_platform:
            packageFilename = '*arm.apk' #the arm.apk is to avoid
                                         #unsigned/unaligned apks
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
            packageFilename = '*.win64-x86_64.zip'
        else:
            return False
        return packageFilename

    def parseFileSize(self, propertyName):
        def getSize(rv, stdout, stderr):
            stdout = stdout.strip()
            return {propertyName: stdout.split()[4]}
        return getSize

    def parseFileHash(self, propertyName):
        def getHash(rv, stdout, stderr):
            stdout = stdout.strip()
            return {propertyName: stdout.split(' ',2)[1]}
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
            property=fileType+'Filename',
            workdir='.',
            name='set_'+fileType.lower()+'_filename',
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty(
            description=['set', fileType.lower(), 'size',],
            doStepIf=doStepIf,
            command=['bash', '-c',
                     WithProperties("ls -l %(filepath)s")],
            workdir='.',
            name='set_'+fileType.lower()+'_size',
            extract_fn = self.parseFileSize(propertyName=fileType+'Size'),
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty(
            description=['set', fileType.lower(), 'hash',],
            doStepIf=doStepIf,
            command=['bash', '-c',
                     WithProperties('openssl ' + 'dgst -' + self.hashType +
                                    ' %(filepath)s')],
            workdir='.',
            name='set_'+fileType.lower()+'_hash',
            extract_fn=self.parseFileHash(propertyName=fileType+'Hash'),
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty(
            description=['unset', 'filepath',],
            doStepIf=doStepIf,
            name='unset_filepath',
            command='echo "filepath:"',
            workdir=directory,
            extract_fn = self.unsetFilepath,
        ))

    def makeHgtoolStep(self, name='hg_update', repo_url=None, wc=None,
            mirrors=None, bundles=None, env=None,
            clone_by_revision=False, rev=None, workdir='build',
            use_properties=True, locks=None):

        if not env:
            env = self.env

        if use_properties:
            env = env.copy()
            env['PROPERTIES_FILE'] = 'buildprops.json'

        cmd = ['python', WithProperties("%(toolsdir)s/buildfarm/utils/hgtool.py")]

        if clone_by_revision:
            cmd.append('--clone-by-revision')

        if mirrors is None and self.baseMirrorUrls:
            mirrors = ["%s/%s" % (url, self.repoPath) for url in self.baseMirrorUrls]

        if mirrors:
            for mirror in mirrors:
                cmd.extend(["--mirror", mirror])

        if bundles is None and self.baseBundleUrls:
            bundles = ["%s/%s.hg" % (url, self.getRepoName(self.repository)) for url in self.baseBundleUrls]

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

        return RetryingShellCommand(
            name=name,
            command=cmd,
            timeout=60*60,
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

class MercurialBuildFactory(MozillaBuildFactory, MockMixin):
    def __init__(self, objdir, platform, configRepoPath, configSubDir,
                 profiledBuild, mozconfig, srcMozconfig=None,
                 productName=None,
                 android_signing=False,
                 buildRevision=None, stageServer=None, stageUsername=None,
                 stageGroup=None, stageSshKey=None, stageBasePath=None,
                 stageProduct=None, post_upload_include_platform=False,
                 ausBaseUploadDir=None, updatePlatform=None,
                 downloadBaseURL=None, ausUser=None, ausSshKey=None,
                 ausHost=None, nightly=False, leakTest=False, leakTarget=None,
                 checkTest=False, valgrindCheck=False,
                 graphServer=None, graphSelector=None, graphBranch=None,
                 baseName=None, uploadPackages=True, uploadSymbols=True,
                 createSnippet=False, createPartial=False, doCleanup=True,
                 packageSDK=False, packageTests=False, mozillaDir=None,
                 enable_ccache=False, stageLogBaseUrl=None,
                 triggeredSchedulers=None, triggerBuilds=False,
                 mozconfigBranch="production", useSharedCheckouts=False,
                 stagePlatform=None, testPrettyNames=False, l10nCheckTest=False,
                 disableSymbols=False,
                 doBuildAnalysis=False,
                 downloadSubdir=None,
                 multiLocale=False,
                 multiLocaleMerge=True,
                 compareLocalesRepoPath=None,
                 compareLocalesTag='RELEASE_AUTOMATION',
                 mozharnessRepoPath=None,
                 mozharnessTag='default',
                 multiLocaleScript=None,
                 multiLocaleConfig=None,
                 mozharnessMultiOptions=None,
                 tooltool_manifest_src=None,
                 tooltool_bootstrap="setup.sh",
                 tooltool_url_list=None,
                 tooltool_script='/tools/tooltool.py',
                 enablePackaging=True,
                 runAliveTests=True,
                 enableInstaller=False,
                 gaiaRepo=None,
                 gaiaRevision=None,
                 gaiaLanguagesFile=None,
                 gaiaLanguagesScript=None,
                 gaiaL10nRoot=None,
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
        self.configSubDir = configSubDir
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
        self.ausBaseUploadDir = ausBaseUploadDir
        self.updatePlatform = updatePlatform
        self.downloadBaseURL = downloadBaseURL
        self.downloadSubdir = downloadSubdir
        self.ausUser = ausUser
        self.ausSshKey = ausSshKey
        self.ausHost = ausHost
        self.nightly = nightly
        self.leakTest = leakTest
        self.leakTarget = leakTarget
        self.checkTest = checkTest
        self.valgrindCheck = valgrindCheck
        self.graphServer = graphServer
        self.graphSelector = graphSelector
        self.graphBranch = graphBranch
        self.baseName = baseName
        self.uploadPackages = uploadPackages
        self.uploadSymbols = uploadSymbols
        self.disableSymbols = disableSymbols
        self.createSnippet = createSnippet
        self.createPartial = createPartial
        self.doCleanup = doCleanup
        self.packageSDK = packageSDK
        self.enablePackaging = enablePackaging
        self.enableInstaller = enableInstaller
        self.packageTests = packageTests
        self.enable_ccache = enable_ccache
        if self.enable_ccache:
            self.env['CCACHE_BASEDIR'] = WithProperties('%(basedir:-)s')
        self.triggeredSchedulers = triggeredSchedulers
        self.triggerBuilds = triggerBuilds
        self.mozconfigBranch = mozconfigBranch
        self.android_signing = android_signing
        self.post_upload_include_platform = post_upload_include_platform
        self.useSharedCheckouts = useSharedCheckouts
        self.testPrettyNames = testPrettyNames
        self.l10nCheckTest = l10nCheckTest
        self.tooltool_manifest_src = tooltool_manifest_src
        self.tooltool_url_list = tooltool_url_list or []
        self.tooltool_script = tooltool_script
        self.tooltool_bootstrap = tooltool_bootstrap
        self.runAliveTests = runAliveTests
        self.gaiaRepo = gaiaRepo
        self.gaiaRevision = gaiaRevision

        assert len(self.tooltool_url_list) <= 1, "multiple urls not currently supported by tooltool"

        if self.uploadPackages:
            assert productName and stageServer and stageUsername
            assert stageBasePath
        if self.createSnippet:
            if 'android' in platform: #happens before we trim platform
                assert downloadSubdir
            assert ausBaseUploadDir and updatePlatform and downloadBaseURL
            assert ausUser and ausSshKey and ausHost

            # To preserve existing behavior, we need to set the
            # ausFullUploadDir differently for when we are create all the
            # mars (complete+partial) ourselves.
            if self.createPartial:
                # e.g.:
                # /opt/aus2/incoming/2/Firefox/mozilla-central/WINNT_x86-msvc
                self.ausFullUploadDir = '%s/%s' % (self.ausBaseUploadDir,
                                                   self.updatePlatform)
            else:
                # this is a tad ugly because we need python interpolation
                # as well as WithProperties, e.g.:
                # /opt/aus2/build/0/Firefox/mozilla-central/WINNT_x86-msvc/2008010103/en-US
                self.ausFullUploadDir = '%s/%s/%%(buildid)s/en-US' % \
                                          (self.ausBaseUploadDir,
                                           self.updatePlatform)

        self.complete_platform = self.platform
        # we don't need the extra cruft in 'platform' anymore
        self.platform = platform.split('-')[0]
        self.stagePlatform = stagePlatform
        # it turns out that the cruft is useful for dealing with multiple types
        # of builds that are all done using the same self.platform.
        # Examples of what happens:
        #   platform = 'linux' sets self.platform_variation to []
        #   platform = 'linux-opt' sets self.platform_variation to ['opt']
        #   platform = 'linux-opt-rpm' sets self.platform_variation to ['opt','rpm']
        platform_chunks = self.complete_platform.split('-', 1)
        if len(platform_chunks) > 1:
                self.platform_variation = platform_chunks[1].split('-')
        else:
                self.platform_variation = []

        assert self.platform in getSupportedPlatforms(), "%s not in %s" % (self.platform, getSupportedPlatforms())

        if self.graphServer is not None:
            self.tbPrint = False
        else:
            self.tbPrint = True

        # SeaMonkey/Thunderbird make use of mozillaDir. Firefox does not.
        if mozillaDir:
            self.mozillaDir = '/%s' % mozillaDir
            self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)
        else:
            self.mozillaDir = ''
            self.mozillaObjdir = self.objdir

        # These following variables are useful for sharing build steps (e.g.
        # update generation) with subclasses that don't use object dirs (e.g.
        # l10n repacks).
        #
        # We also concatenate the baseWorkDir at the outset to avoid having to
        # do that everywhere.
        self.mozillaSrcDir = '.%s' % self.mozillaDir
        self.absMozillaSrcDir = '%s%s' % (self.baseWorkDir, self.mozillaDir)
        self.absMozillaObjDir = '%s/%s' % (self.baseWorkDir, self.mozillaObjdir)

        self.latestDir = '/pub/mozilla.org/%s' % self.stageProduct + \
                         '/nightly/latest-%s' % self.branchName
        if self.post_upload_include_platform:
            self.latestDir += '-%s' % self.stagePlatform

        self.stageLogBaseUrl = stageLogBaseUrl
        if self.stageLogBaseUrl:
            # yes, the branchName is needed twice here so that log uploads work for all
            self.logUploadDir = '%s/%s-%s/' % (self.branchName, self.branchName,
                                               self.stagePlatform)
            self.logBaseUrl = '%s/%s' % (self.stageLogBaseUrl, self.logUploadDir)
        else:
            self.logUploadDir = 'tinderbox-builds/%s-%s/' % (self.branchName,
                                                             self.stagePlatform)
            self.logBaseUrl = 'http://%s/pub/mozilla.org/%s/%s' % \
                        ( self.stageServer, self.stageProduct, self.logUploadDir)

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
            self.addMozharnessRepoSteps()
        if multiLocale:
            assert compareLocalesRepoPath and compareLocalesTag
            assert mozharnessRepoPath and mozharnessTag
            assert multiLocaleScript and multiLocaleConfig
            self.compareLocalesRepoPath = compareLocalesRepoPath
            self.compareLocalesTag = compareLocalesTag
            self.multiLocaleScript = multiLocaleScript
            self.multiLocaleConfig = multiLocaleConfig
            self.multiLocaleMerge = multiLocaleMerge
            if mozharnessMultiOptions:
                self.mozharnessMultiOptions = mozharnessMultiOptions
            else:
                self.mozharnessMultiOptions = ['--only-pull-locale-source',
                                               '--only-add-locales',
                                               '--only-package-multi']

            self.addMultiLocaleRepoSteps()

        self.multiLocale = multiLocale

        if gaiaLanguagesFile:
            assert gaiaLanguagesScript and gaiaL10nRoot
            self.gaiaLanguagesFile = gaiaLanguagesFile
            self.gaiaLanguagesScript = gaiaLanguagesScript
            self.gaiaL10nRoot = gaiaL10nRoot
            self.gaiaL10nBaseDir = WithProperties('%(basedir)s/build-gaia-l10n')
            self.env['LOCALE_BASEDIR'] = self.gaiaL10nBaseDir
            self.env['LOCALES_FILE'] = self.gaiaLanguagesFile

        self.addBuildSteps()
        if self.uploadSymbols or (not self.disableSymbols and (self.packageTests or self.leakTest)):
            self.addBuildSymbolsStep()
        if self.uploadSymbols:
            self.addUploadSymbolsStep()
        if self.enablePackaging or self.uploadPackages:
            self.addPackageSteps()
        if self.uploadPackages:
            self.addUploadSteps()
        if self.testPrettyNames:
            self.addTestPrettyNamesSteps()
        if self.leakTest:
            self.addLeakTestSteps()
        if self.l10nCheckTest:
            self.addL10nCheckTestSteps()
        if self.checkTest:
            self.addCheckTestSteps()
        if self.valgrindCheck:
            self.addValgrindCheckSteps()
        if self.createSnippet:
            self.addUpdateSteps()
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
        name=self.mozharnessRepoPath.rstrip('/').split('/')[-1]
        self.addStep(ShellCommand(
            name='rm_%s'%name,
            command=['rm', '-rf', '%s' % name],
            description=['removing', name],
            descriptionDone=['remove', name],
            haltOnFailure=True,
            workdir='.',
        ))
        self.addStep(MercurialCloneCommand(
            name='hg_clone_%s' % name,
            command=['hg', 'clone', self.getRepository(self.mozharnessRepoPath), name],
            description=['checking', 'out', name],
            descriptionDone=['checkout', name],
            haltOnFailure=True,
            workdir='.',
        ))
        self.addStep(ShellCommand(
            name='hg_update_%s'% name,
            command=['hg', 'update', '-r', self.mozharnessTag],
            description=['updating', name, 'to', self.mozharnessTag],
            workdir=name,
            haltOnFailure=True
        ))

    def addMultiLocaleRepoSteps(self):
        name=self.compareLocalesRepoPath.rstrip('/').split('/')[-1]
        self.addStep(ShellCommand(
            name='rm_%s'%name,
            command=['rm', '-rf', '%s' % name],
            description=['removing', name],
            descriptionDone=['remove', name],
            haltOnFailure=True,
            workdir='.',
        ))
        self.addStep(MercurialCloneCommand(
            name='hg_clone_%s' % name,
            command=['hg', 'clone', self.getRepository(self.compareLocalesRepoPath), name],
            description=['checking', 'out', name],
            descriptionDone=['checkout', name],
            haltOnFailure=True,
            workdir='.',
        ))
        self.addStep(ShellCommand(
            name='hg_update_%s'% name,
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
        self.addDoBuildSteps()
        if self.signingServers and self.enableSigning:
            self.addGetTokenSteps()
        if self.doBuildAnalysis:
            self.addBuildAnalysisSteps()

    def addPreBuildSteps(self):
        if self.nightly:
            self.addStep(ShellCommand(
             name='rm_builddir',
             command=['rm', '-rf', 'build'],
             env=self.env,
             workdir='.',
             timeout=60*60 # 1 hour
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
        if self.nightly:
            self.addStep(ShellCommand(
             name='rm_old_symbols',
             command="find 20* -maxdepth 2 -mtime +7 -exec rm -rf {} \;",
             env=self.env,
             workdir='.',
             description=['cleanup', 'old', 'symbols'],
             flunkOnFailure=False
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
                name = 'set_got_revision',
                command=['hg', 'parent', '--template={node}'],
                extract_fn = short_hash
            ))
        else:
            self.addStep(Mercurial(
             name='hg_update',
             mode='update',
             baseURL='http://%s/' % self.hgHost,
             defaultBranch=self.repoPath,
             timeout=60*60, # 1 hour
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
            self.addStep(self.makeHgtoolStep(
                name="gaia_sources",
                rev=self.gaiaRevision or 'default',
                repo_url="http://%s/%s" % (self.hgHost, self.gaiaRepo),
                workdir="build/",
                use_properties=False,
                mirrors=['%s/%s' % (url, self.gaiaRepo) for url in self.baseMirrorUrls],
                bundles=[],
                wc='gaia',
            ))
            if self.gaiaLanguagesFile:
                languagesFile = '%(basedir)s/build/gaia/' + self.gaiaLanguagesFile
                # call mozharness script that will checkout all of the repos
                # it should only need the languages file path passed to it
                # need to figure out what to pass to the build system to make
                # gaia create a multilocale profile, too
                self.addStep(MockCommand(
                    name='clone_gaia_l10n_repos',
                    command=['python', 'mozharness/%s' % self.gaiaLanguagesScript,
                             '--gaia-languages-file', WithProperties(languagesFile),
                             '--gaia-l10n-root', self.gaiaL10nRoot,
                             '--gaia-l10n-base-dir', self.gaiaL10nBaseDir],
                    env=self.env,
                    workdir=WithProperties('%(basedir)s'),
                    haltOnFailure=True,
                    mock=self.use_mock,
                    target=self.mock_target,
                    mock_workdir_prefix=None,
                ))
        self.addStep(SetBuildProperty(
            name='set_comments',
            property_name="comments",
            value=lambda build:build.source.changes[-1].comments if len(build.source.changes) > 0 else "",
        ))

    def addConfigSteps(self):
        assert self.configRepoPath is not None
        assert self.configSubDir is not None
        assert self.mozconfig is not None

        configRepo = self.getRepository(self.configRepoPath)
        hg_mozconfig = '%s/raw-file/%s/%s/%s/mozconfig' % (
                configRepo, self.mozconfigBranch, self.configSubDir, self.mozconfig)
        if self.srcMozconfig:
            cmd = ['bash', '-c',
                    '''if [ -f "%(src_mozconfig)s" ]; then
                        echo Using in-tree mozconfig;
                        cp %(src_mozconfig)s .mozconfig;
                    else
                        echo Downloading mozconfig;
                        wget -O .mozconfig %(hg_mozconfig)s;
                    fi'''.replace("\n","") % {'src_mozconfig': self.srcMozconfig, 'hg_mozconfig': hg_mozconfig}]
        else:
            cmd = ['wget', '-O', '.mozconfig', hg_mozconfig]

        self.addStep(RetryingShellCommand(
            name='get_mozconfig',
            command=cmd,
            description=['getting', 'mozconfig'],
            descriptionDone=['got', 'mozconfig'],
            haltOnFailure=True
        ))
        self.addStep(ShellCommand(
         name='cat_mozconfig',
         command=['cat', '.mozconfig'],
        ))
        if self.tooltool_manifest_src:
            self.addStep(ShellCommand(
                name='run_tooltool',
                command=[
                    WithProperties('%(toolsdir)s/scripts/tooltool/fetch_and_unpack.sh'),
                    self.tooltool_manifest_src,
                    self.tooltool_url_list[0],
                    self.tooltool_script,
                    self.tooltool_bootstrap
                ],
                haltOnFailure=True,
            ))


    def addDoBuildSteps(self):
        workdir=WithProperties('%(basedir)s/build')
        if self.platform.startswith('win'):
            workdir="build"
        command = self.makeCmd + ['-f', 'client.mk', 'build',
                   WithProperties('MOZ_BUILD_DATE=%(buildid:-)s')]

        if self.profiledBuild:
            command.append('MOZ_PGO=1')
        self.addStep(MockCommand(
         name='compile',
         command=command,
         description=['compile'],
         env=self.env,
         haltOnFailure=True,
         timeout=10800,
         # bug 650202 'timeout=7200', bumping to stop the bleeding while we diagnose
         # the root cause of the linker time out.
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
            self.addStep(SetProperty(
                command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
                'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
                'App', 'BuildID'],
                property='buildid',
                workdir='.',
                description=['getting', 'buildid'],
                descriptionDone=['got', 'buildid'],
            ))
            self.addStep(SetProperty(
                command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
                'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
                'App', 'SourceStamp'],
                property='sourcestamp',
                workdir='.',
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
                    testresults = [ ('num_ctors', 'num_ctors', num_ctors, str(num_ctors)) ]
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
                self.addStep(JSONPropertiesDownload(slavedest="properties.json"))
                gs_env = self.env.copy()
                gs_env['PYTHONPATH'] = WithProperties('%(toolsdir)s/lib/python')
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
                    data=WithProperties('TinderboxPrint: num_ctors: %(num_ctors:-unknown)s'),
                    ))

    def addLeakTestStepsCommon(self, baseUrl, leakEnv, graphAndUpload):
        """ Helper function for the two implementations of addLeakTestSteps.
        * baseUrl Base of the URL where we get the old logs.
        * leakEnv Environment used for running firefox.
        * graphAndUpload Used to prevent the try jobs from doing talos graph
          posts and uploading logs."""

        if self.leakTarget:
            self.addStep(MockCommand(
                name='make_%s' % self.leakTarget,
                env=leakEnv,
                workdir='build/%s' % self.objdir,
                command=self.makeCmd + [self.leakTarget],
                warnOnFailure=True,
                haltOnFailure=True,
                mock=self.use_mock,
                target=self.mock_target,
            ))

        if self.runAliveTests:
            self.addStep(MockAliveTest(
              env=leakEnv,
              workdir='build/%s/_leaktest' % self.mozillaObjdir,
              extraArgs=['-register'],
              warnOnFailure=True,
              haltOnFailure=True,
              mock=self.use_mock,
              target=self.mock_target,
              ))
            self.addStep(MockAliveTest(
              env=leakEnv,
              workdir='build/%s/_leaktest' % self.mozillaObjdir,
              warnOnFailure=True,
              haltOnFailure=True,
              mock=self.use_mock,
              target=self.mock_target,
              ))
            self.addStep(MockAliveTest(
              env=leakEnv,
              workdir='build/%s/_leaktest' % self.mozillaObjdir,
              extraArgs=['--trace-malloc', 'malloc.log',
                         '--shutdown-leaks=sdleak.log'],
              timeout=3600, # 1 hour, because this takes a long time on win32
              warnOnFailure=True,
              haltOnFailure=True,
              mock=self.use_mock,
              target=self.mock_target,
              ))

        # Download and unpack the old versions of malloc.log and sdleak.tree
        cmd = ['bash', '-c',
                WithProperties('%(toolsdir)s/buildfarm/utils/wget_unpack.sh ' +
                               baseUrl + ' logs.tar.gz '+
                               'malloc.log:malloc.log.old sdleak.tree:sdleak.tree.old') ]
        self.addStep(ShellCommand(
            name='get_logs',
            env=self.env,
            workdir='.',
            command=cmd,
        ))

        logdir = "%s/_leaktest" % self.mozillaObjdir
        if 'thunderbird' in self.stageProduct:
            logdir = self.objdir

        self.addStep(ShellCommand(
          name='mv_malloc_log',
          env=self.env,
          command=['mv',
                   '%s/malloc.log' % logdir,
                   '../malloc.log'],
          ))
        self.addStep(ShellCommand(
          name='mv_sdleak_log',
          env=self.env,
          command=['mv',
                   '%s/sdleak.log' % logdir,
                   '../sdleak.log'],
          ))
        self.addStep(MockCompareLeakLogs(
          name='compare_current_leak_log',
          mallocLog='../malloc.log',
          platform=self.platform,
          env=self.env,
          objdir=self.mozillaObjdir,
          testname='current',
          tbPrint=self.tbPrint,
          warnOnFailure=True,
          haltOnFailure=True,
          workdir='build',
          mock=self.use_mock,
          target=self.mock_target,
          ))
        if self.graphServer and graphAndUpload:
            gs_env = self.env.copy()
            gs_env['PYTHONPATH'] = WithProperties('%(toolsdir)s/lib/python')
            self.addBuildInfoSteps()
            self.addStep(JSONPropertiesDownload(slavedest="properties.json"))
            self.addStep(GraphServerPost(server=self.graphServer,
                                         selector=self.graphSelector,
                                         branch=self.graphBranch,
                                         resultsname=self.baseName,
                                         env=gs_env,
                                         propertiesFile="properties.json"))
        self.addStep(MockCompareLeakLogs(
          name='compare_previous_leak_log',
          mallocLog='../malloc.log.old',
          platform=self.platform,
          env=self.env,
          objdir=self.mozillaObjdir,
          testname='previous',
          workdir='build',
          mock=self.use_mock,
          target=self.mock_target,
          ))
        self.addStep(ShellCommand(
          name='create_sdleak_tree',
          env=self.env,
          workdir='.',
          command=['bash', '-c',
                   'perl build%s/tools/trace-malloc/diffbloatdump.pl '
                   '--depth=15 --use-address /dev/null sdleak.log '
                   '> sdleak.tree' % self.mozillaDir],
          warnOnFailure=True,
          haltOnFailure=True
          ))
        if self.platform in ('macosx', 'macosx64', 'linux', 'linux64'):
            self.addStep(ShellCommand(
              name='create_sdleak_raw',
              env=self.env,
              workdir='.',
              command=['mv', 'sdleak.tree', 'sdleak.tree.raw']
              ))
            # Bug 571443 - disable fix-macosx-stack.pl
            if self.platform == 'macosx64':
                self.addStep(ShellCommand(
                  workdir='.',
                  command=['cp', 'sdleak.tree.raw', 'sdleak.tree'],
                  ))
            else:
                self.addStep(ShellCommand(
                  name='get_fix_stack',
                  env=self.env,
                  workdir='.',
                  command=['/bin/bash', '-c',
                           'perl '
                           'build%s/tools/rb/fix_stack_using_bpsyms.py '
                           'sdleak.tree.raw '
                           '> sdleak.tree' % self.mozillaDir,
                           ],
                  warnOnFailure=True,
                  haltOnFailure=True
                    ))
        if graphAndUpload:
            cmd = ['bash', '-c',
                    WithProperties('%(toolsdir)s/buildfarm/utils/pack_scp.sh ' +
                        'logs.tar.gz ' + ' .. ' +
                        '%s ' % self.stageUsername +
                        '%s ' % self.stageSshKey +
                        # Notice the '/' after the ':'. This prevents windows from trying to modify
                        # the path
                        '%s:/%s/%s ' % (self.stageServer, self.stageBasePath,
                        self.logUploadDir) +
                        'malloc.log sdleak.tree') ]
            self.addStep(ShellCommand(
                name='upload_logs',
                env=self.env,
                command=cmd,
                ))
        self.addStep(ShellCommand(
          name='compare_sdleak_tree',
          env=self.env,
          workdir='.',
          command=['perl', 'build%s/tools/trace-malloc/diffbloatdump.pl' % self.mozillaDir,
                   '--depth=15', 'sdleak.tree.old', 'sdleak.tree']
          ))

    def addLeakTestSteps(self):
        leakEnv = self.env.copy()
        leakEnv['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        leakEnv['MINIDUMP_SAVE_PATH'] = WithProperties('%(basedir:-)s/minidumps')
        self.addLeakTestStepsCommon(self.logBaseUrl, leakEnv, True)

    def addCheckTestSteps(self):
        env = self.env.copy()
        env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        env['MINIDUMP_SAVE_PATH'] = WithProperties('%(basedir:-)s/minidumps')
        self.addStep(MockMozillaCheck(
         test_name="check",
         makeCmd=self.makeCmd,
         warnOnWarnings=True,
         workdir=WithProperties('%(basedir)s/build/' + self.objdir),
         timeout=5*60, # 5 minutes.
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
         timeout=5*60, # 5 minutes.
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
        env = self.env
        if 'MOZ_SIGN_CMD' in env:
            env = env.copy()
            del env['MOZ_SIGN_CMD']

        if 'mac' in self.platform:
            # Need to run this target or else the packaging targets will
            # fail.
            self.addStep(ShellCommand(
             name='postflight_all',
             command=self.makeCmd + ['-f', 'client.mk', 'postflight_all'],
             env=env,
             haltOnFailure=False,
             flunkOnFailure=False,
             warnOnFailure=False,
            ))
        pkg_targets = ['package']
        if self.enableInstaller:
            pkg_targets.append('installer')
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
             env=env,
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
                command=self.makeCmd + ['l10n-check', 'MOZ_PKG_PRETTYNAMES=1'],
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
        if self.android_signing:
            pkg_env['JARSIGNER'] = WithProperties('%(toolsdir)s/release/signing/mozpass.py')

        objdir = WithProperties('%(basedir)s/build/' + self.objdir)
        if self.platform.startswith('win'):
            objdir = "build/%s" % self.objdir
        workdir = WithProperties('%(basedir)s/build')
        if self.platform.startswith('win'):
            workdir = "build/"
        if 'rpm' in self.platform_variation:
            pkgArgs.append("MOZ_PKG_FORMAT=RPM")
        if self.packageSDK:
            self.addStep(MockCommand(
             name='make_sdk',
             command=self.makeCmd + ['-f', 'client.mk', 'sdk'],
             env=pkg_env,
             workdir=workdir,
             mock=self.use_mock,
             target=self.mock_target,
             haltOnFailure=True,
             mock_workdir_prefix=None,
            ))
        if self.packageTests:
            self.addStep(MockCommand(
             name='make_pkg_tests',
             command=self.makeCmd + ['package-tests'] + pkgTestArgs,
             env=pkg_env,
             workdir=objdir,
             mock=self.use_mock,
             target=self.mock_target,
             mock_workdir_prefix=None,
             haltOnFailure=True,
            ))
        self.addStep(MockCommand(
            name='make_pkg',
            command=self.makeCmd + ['package'] + pkgArgs,
            env=pkg_env,
            workdir=objdir,
            mock=self.use_mock,
            target=self.mock_target,
            mock_workdir_prefix=None,
            haltOnFailure=True,
        ))

        # Get package details
        self.packageFilename = self.getPackageFilename(self.platform,
                                                  self.platform_variation)
        if self.packageFilename and 'rpm' not in self.platform_variation and self.productName not in ('xulrunner', 'b2g'):
            self.addFilePropertiesSteps(filename=self.packageFilename,
                                        directory='build/%s/dist' % self.mozillaObjdir,
                                        fileType='package',
                                        haltOnFailure=True)
        # Windows special cases
        if self.enableInstaller and self.productName != 'xulrunner':
            self.addStep(ShellCommand(
                name='make_installer',
                command=self.makeCmd + ['installer'] + pkgArgs,
                env=pkg_env,
                workdir='build/%s' % self.objdir,
                haltOnFailure=True
            ))
            self.addFilePropertiesSteps(filename='*.installer.exe',
                                        directory='build/%s/dist/install/sea' % self.mozillaObjdir,
                                        fileType='installer',
                                        haltOnFailure=True)

        if self.productName == 'xulrunner':
            self.addStep(SetProperty(
                command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
                         'build/%s/dist/bin/platform.ini' % self.mozillaObjdir,
                         'Build', 'BuildID'],
                property='buildid',
                workdir='.',
                name='get_build_id',
            ))
        else:
            self.addStep(SetProperty(
                command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
                         'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
                         'App', 'BuildID'],
                property='buildid',
                workdir='.',
                name='get_build_id',
            ))
            self.addStep(SetProperty(
                command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
                         'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
                         'App', 'Version'],
                property='appVersion',
                workdir='.',
                name='get_app_version',
            ))
            self.addStep(SetProperty(
                command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
                         'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
                         'App', 'Name'],
                property='appName',
                workdir='.',
                name='get_app_name',
            ))
        self.pkg_env = pkg_env

    def addUploadSteps(self):
        if self.multiLocale:
            self.doUpload(postUploadBuildDir='en-US')
            cmd = ['python', 'mozharness/%s' % self.multiLocaleScript,
                   '--config-file', self.multiLocaleConfig]
            if self.multiLocaleMerge:
                cmd.append('--merge-locales')
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
            # We need to set packageFilename to the multi apk
            self.addFilePropertiesSteps(filename=self.packageFilename,
                                        directory='build/%s/dist' % self.mozillaObjdir,
                                        fileType='package',
                                        haltOnFailure=True)

        if self.createSnippet and 'android' not in self.complete_platform:
            self.addCreateUpdateSteps();

        # Call out to a subclass to do the actual uploading
        self.doUpload(uploadMulti=self.multiLocale)

    def addCreateSnippetsSteps(self, milestone_extra=''):
        if 'android' in self.complete_platform:
            cmd = [
                'python',
                WithProperties('%(toolsdir)s/scripts/android/android_snippet.py'),
                '--download-base-url', self.downloadBaseURL,
                '--download-subdir', self.downloadSubdir,
                '--abi', self.updatePlatform,
                '--aus-base-path', self.ausBaseUploadDir,
                '--aus-host', self.ausHost,
                '--aus-user', self.ausUser,
                '--aus-ssh-key', '~/.ssh/%s' % self.ausSshKey,
                WithProperties(self.objdir + '/dist/%(packageFilename)s')
            ]
            self.addStep(ShellCommand(
                name='create_android_snippet',
                command=cmd,
                haltOnFailure=False,
            ))
        else:
            milestone = self.branchName + milestone_extra
            self.addStep(CreateCompleteUpdateSnippet(
                name='create_complete_snippet',
                objdir=self.absMozillaObjDir,
                milestone=milestone,
                baseurl='%s/nightly' % self.downloadBaseURL,
                hashType=self.hashType,
            ))
            self.addStep(ShellCommand(
                name='cat_complete_snippet',
                description=['cat', 'complete', 'snippet'],
                command=['cat', 'complete.update.snippet'],
                workdir='%s/dist/update' % self.absMozillaObjDir,
            ))

    def addUploadSnippetsSteps(self):
        self.addStep(RetryingShellCommand(
            name='create_aus_updir',
            command=['bash', '-c',
                     WithProperties('ssh -l %s ' % self.ausUser +
                                    '-i ~/.ssh/%s %s ' % (self.ausSshKey,self.ausHost) +
                                    'mkdir -p %s' % self.ausFullUploadDir)],
            description=['create', 'aus', 'upload', 'dir'],
            haltOnFailure=True,
        ))
        self.addStep(RetryingShellCommand(
            name='upload_complete_snippet',
            command=['scp', '-o', 'User=%s' % self.ausUser,
                     '-o', 'IdentityFile=~/.ssh/%s' % self.ausSshKey,
                     'dist/update/complete.update.snippet',
                     WithProperties('%s:%s/complete.txt' % (self.ausHost,
                                                            self.ausFullUploadDir))],
             workdir=self.absMozillaObjDir,
             description=['upload', 'complete', 'snippet'],
             haltOnFailure=True,
        ))

    def addSubmitBalrogUpdates(self):
        if self.balrog_api_root:
            self.addStep(JSONPropertiesDownload(
                name='download_balrog_props',
                slavedest='buildprops_balrog.json',
                workdir='.',
                flunkOnFailure=False,
            ))
            cmd = [
                self.env.get('PYTHON26', 'python'),
                WithProperties('%(toolsdir)s/scripts/updates/balrog-client.py'),
                '--build-properties', 'buildprops_balrog.json',
                '--api-root', self.balrog_api_root,
                '--verbose',
            ]
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
                flunkOnFailure=False,
            ))

    def addUpdateSteps(self):
        self.addCreateSnippetsSteps()
        self.addUploadSnippetsSteps()
        self.addSubmitBalrogUpdates()

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
         timeout=60*60,
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
         timeout=2400, # 40 minutes
        ))

    def addPostBuildCleanupSteps(self):
        if self.nightly:
            self.addStep(ShellCommand(
             name='rm_builddir',
             command=['rm', '-rf', 'build'],
             env=self.env,
             workdir='.',
             timeout=5400 # 1.5 hours
            ))

class TryBuildFactory(MercurialBuildFactory):
    def __init__(self,talosMasters=None, unittestMasters=None, packageUrl=None,
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

        self.leakTestReferenceRepo = 'mozilla-central'
        if 'thunderbird' in kwargs['stageProduct']:
            self.leakTestReferenceRepo = 'comm-central'

        MercurialBuildFactory.__init__(self, **kwargs)

    def addSourceSteps(self):
        if self.useSharedCheckouts:
            # We normally rely on the Mercurial step to clobber for us, but
            # since we're managing the checkout ourselves...
            self.addStep(ShellCommand(
                name='clobber_build',
                command=['rm', '-rf', 'build'],
                workdir='.',
                timeout=60*60,
            ))
            self.addStep(JSONPropertiesDownload(
                name="download_props",
                slavedest="buildprops.json",
                workdir='.'
            ))

            step = self.makeHgtoolStep(
                    clone_by_revision=True,
                    wc='build',
                    workdir='.',
                    locks=[hg_try_lock.access('counting')],
                    )
            self.addStep(step)

        else:
            self.addStep(Mercurial(
            name='hg_update',
            mode='clobber',
            baseURL='http://%s/' % self.hgHost,
            defaultBranch=self.repoPath,
            timeout=60*60, # 1 hour
            locks=[hg_try_lock.access('counting')],
            ))

        if self.buildRevision:
            self.addStep(ShellCommand(
             name='hg_update',
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             haltOnFailure=True
            ))
        self.addStep(SetProperty(
         name = 'set_got_revision',
         command=['hg', 'parent', '--template={node}'],
         extract_fn = short_hash
        ))
        self.addStep(SetBuildProperty(
            name='set_comments',
            property_name="comments",
            value=lambda build:build.source.changes[-1].comments if len(build.source.changes) > 0 else "",
        ))

    def addLeakTestSteps(self):
        leakEnv = self.env.copy()
        leakEnv['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        leakEnv['MINIDUMP_SAVE_PATH'] = WithProperties('%(basedir:-)s/minidumps')
        baseUrl = 'http://%s/pub/mozilla.org/%s/tinderbox-builds/%s-%s' % \
            (self.stageServer, self.productName, self.leakTestReferenceRepo, self.complete_platform)
        self.addLeakTestStepsCommon(baseUrl, leakEnv, False)

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        self.addStep(SetBuildProperty(
             name='set_who',
             property_name='who',
             value=lambda build:str(build.source.changes[0].who) if len(build.source.changes) > 0 else "nobody@example.com",
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
                revision=WithProperties('%(got_revision)s'),
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
        self.addStep(RetryingMockProperty(
             command=self.makeCmd + ['upload'],
             env=uploadEnv,
             workdir=objdir,
             mock=self.use_mock,
             target=self.mock_target,
             extract_fn = parse_make_upload,
             haltOnFailure=True,
             description=["upload"],
             timeout=40*60, # 40 minutes
             log_eval_func=lambda c,s: regex_log_evaluator(c, s, upload_errors),
             locks=[upload_lock.access('counting')],
             mock_workdir_prefix=None,
        ))

        talosBranch = "%s-%s-talos" % (self.branchName, self.complete_platform)
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
             revision=WithProperties('%(got_revision)s'),
             files=[WithProperties('%(packageUrl)s')],
             user=WithProperties('%(who)s'),
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

    def getCompleteMarPatternMatch(self):
        marPattern = getPlatformFtpDir(self.platform)
        if not marPattern:
            return False
        marPattern += '.complete.mar'
        return marPattern

    def previousMarExists(self, step):
        return step.build.getProperties().has_key("previousMarFilename") and len(step.build.getProperty("previousMarFilename")) > 0;

    def addCreatePartialUpdateSteps(self, extraArgs=None):
        '''This function expects that the following build properties are
           already set: buildid, completeMarFilename
        '''
        self.makePartialTools()
        # These tools (mar+mbsdiff) should now be built.
        mar='../dist/host/bin/mar'
        mbsdiff='../dist/host/bin/mbsdiff'
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
                     WithProperties('ssh -l %s -i ~/.ssh/%s %s ' % (self.stageUsername,
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
        previousMarURL = WithProperties('http://%s' % self.stageServer + \
                          '%s' % self.latestDir + \
                          '/%(previousMarFilename)s')
        self.addStep(RetryingMockCommand(
            name='get_previous_mar',
            description=['get', 'previous', 'mar'],
            doStepIf = self.previousMarExists,
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
            doStepIf = self.previousMarExists,
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
            doStepIf = self.previousMarExists,
            filename='application.ini',
            directory='previous',
            filetype='file',
            max_depth=4,
            property_name='previous_inipath',
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        self.addStep(SetProperty(
            name='set_previous_buildid',
            description=['set', 'previous', 'buildid'],
            doStepIf = self.previousMarExists,
            command=['python',
                     '%s/config/printconfigsetting.py' % self.absMozillaSrcDir,
                     WithProperties(self.absMozillaObjDir + '/%(previous_inipath)s'),
                     'App', 'BuildID'],
            property='previous_buildid',
            workdir='.',
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
        partialMarCommand=self.makeCmd + ['-C',
                           'tools/update-packaging', 'partial-patch',
                           'STAGE_DIR=../../dist/update',
                           'SRC_BUILD=../../previous',
                           WithProperties('SRC_BUILD_ID=%(previous_buildid)s'),
                           'DST_BUILD=../../current',
                           WithProperties('DST_BUILD_ID=%(buildid)s')]
        if extraArgs is not None:
            partialMarCommand.extend(extraArgs)
        self.addStep(MockCommand(
            name='make_partial_mar',
            description=self.makeCmd + ['partial', 'mar'],
            doStepIf = self.previousMarExists,
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
            doStepIf = self.previousMarExists,
            command=['rm', '-rf', 'previous.mar'],
            env=self.env,
            workdir='%s/dist/update' % self.absMozillaObjDir,
            haltOnFailure=True,
        ))
        # Update the build properties to pickup information about the partial.
        self.addFilePropertiesSteps(
            filename='*.partial.*.mar',
            doStepIf = self.previousMarExists,
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

    def addCreateSnippetsSteps(self, milestone_extra=''):
        MercurialBuildFactory.addCreateSnippetsSteps(self, milestone_extra)
        milestone = self.branchName + milestone_extra
        if self.createPartial:
            self.addStep(CreatePartialUpdateSnippet(
                name='create_partial_snippet',
                doStepIf = self.previousMarExists,
                objdir=self.absMozillaObjDir,
                milestone=milestone,
                baseurl='%s/nightly' % self.downloadBaseURL,
                hashType=self.hashType,
            ))
            self.addStep(ShellCommand(
                name='cat_partial_snippet',
                description=['cat', 'partial', 'snippet'],
                doStepIf = self.previousMarExists,
                command=['cat', 'partial.update.snippet'],
                workdir='%s/dist/update' % self.absMozillaObjDir,
            ))

    def getPreviousBuildUploadDir(self):
        # Uploading the complete snippet occurs regardless of whether we are
        # generating partials on the slave or not, it just goes to a different
        # path for eventual consumption by the central update generation
        # server.

        # ausFullUploadDir is expected to point to the correct base path on the
        # AUS server for each case:
        #
        # updates generated centrally: /opt/aus2/build/0/...
        # updates generated on slave:  /opt/aus2/incoming/2/...
        if self.createPartial:
            return "%s/%%(previous_buildid)s/en-US" % \
                                         self.ausFullUploadDir
        else:
            return self.ausFullUploadDir

    def getCurrentBuildUploadDir(self):
        if self.createPartial:
            return "%s/%%(buildid)s/en-US" % self.ausFullUploadDir
        else:
            return self.ausFullUploadDir

    def addUploadSnippetsSteps(self):
        ausPreviousBuildUploadDir = self.getPreviousBuildUploadDir()
        self.addStep(RetryingShellCommand(
            name='create_aus_previous_updir',
            doStepIf = self.previousMarExists,
            command=['bash', '-c',
                     WithProperties('ssh -l %s ' %  self.ausUser +
                                    '-i ~/.ssh/%s %s ' % (self.ausSshKey,self.ausHost) +
                                    'mkdir -p %s' % ausPreviousBuildUploadDir)],
            description=['create', 'aus', 'previous', 'upload', 'dir'],
            haltOnFailure=True,
            ))
        self.addStep(RetryingShellCommand(
            name='upload_complete_snippet',
            description=['upload', 'complete', 'snippet'],
            doStepIf = self.previousMarExists,
            command=['scp', '-o', 'User=%s' % self.ausUser,
                     '-o', 'IdentityFile=~/.ssh/%s' % self.ausSshKey,
                     'dist/update/complete.update.snippet',
                     WithProperties('%s:%s/complete.txt' % (self.ausHost,
                                                            ausPreviousBuildUploadDir))],
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
        ))

        # We only need to worry about empty snippets (and partials obviously)
        # if we are creating partial patches on the slaves.
        if self.createPartial:
            self.addStep(RetryingShellCommand(
                name='upload_partial_snippet',
                doStepIf = self.previousMarExists,
                command=['scp', '-o', 'User=%s' % self.ausUser,
                         '-o', 'IdentityFile=~/.ssh/%s' % self.ausSshKey,
                         'dist/update/partial.update.snippet',
                         WithProperties('%s:%s/partial.txt' % (self.ausHost,
                                                               ausPreviousBuildUploadDir))],
                workdir=self.absMozillaObjDir,
                description=['upload', 'partial', 'snippet'],
                haltOnFailure=True,
            ))
            ausCurrentBuildUploadDir = self.getCurrentBuildUploadDir()
            self.addStep(RetryingShellCommand(
                name='create_aus_current_updir',
                doStepIf = self.previousMarExists,
                command=['bash', '-c',
                         WithProperties('ssh -l %s ' %  self.ausUser +
                                        '-i ~/.ssh/%s %s ' % (self.ausSshKey,self.ausHost) +
                                        'mkdir -p %s' % ausCurrentBuildUploadDir)],
                description=['create', 'aus', 'current', 'upload', 'dir'],
                haltOnFailure=True,
            ))
            # Create remote empty complete/partial snippets for current build.
            # Also touch the remote platform dir to defeat NFS caching on the
            # AUS webheads.
            self.addStep(RetryingShellCommand(
                name='create_empty_snippets',
                doStepIf = self.previousMarExists,
                command=['bash', '-c',
                         WithProperties('ssh -l %s ' %  self.ausUser +
                                        '-i ~/.ssh/%s %s ' % (self.ausSshKey,self.ausHost) +
                                        'touch %s/complete.txt %s/partial.txt %s' % (ausCurrentBuildUploadDir,
                                                                                     ausCurrentBuildUploadDir,
                                                                                     self.ausFullUploadDir))],
                description=['create', 'empty', 'snippets'],
                haltOnFailure=True,
            ))

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        # Because of how the RPM packaging works,
        # we need to tell make upload to look for RPMS
        if 'rpm' in self.complete_platform:
            upload_vars = ["MOZ_PKG_FORMAT=RPM"]
        else:
            upload_vars = []
        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_TO_TEMP': '1'})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        # Always upload builds to the dated tinderbox builds directories
        if self.tinderboxBuildsDir is None:
            tinderboxBuildsDir = "%s-%s" % (self.branchName, self.stagePlatform)
        else:
            tinderboxBuildsDir = self.tinderboxBuildsDir

        uploadArgs = dict(
                upload_dir=tinderboxBuildsDir,
                product=self.stageProduct,
                buildid=WithProperties("%(buildid)s"),
                revision=WithProperties("%(got_revision)s"),
                as_list=False,
            )
        if self.hgHost.startswith('ssh'):
            uploadArgs['to_shadow'] = True
            uploadArgs['to_tinderbox_dated'] = False
        else:
            uploadArgs['to_shadow'] = False
            uploadArgs['to_tinderbox_dated'] = True

        if self.nightly:
            uploadArgs['to_dated'] = True
            if 'rpm' in self.complete_platform:
                uploadArgs['to_latest'] = False
            else:
                uploadArgs['to_latest'] = True
            if self.post_upload_include_platform:
                # This was added for bug 557260 because of a requirement for
                # mobile builds to upload in a slightly different location
                uploadArgs['branch'] = '%s-%s' % (self.branchName, self.stagePlatform)
            else:
                uploadArgs['branch'] = self.branchName
        if uploadMulti:
            upload_vars.append("AB_CD=multi")
        if postUploadBuildDir:
            uploadArgs['builddir'] = postUploadBuildDir
        uploadEnv['POST_UPLOAD_CMD'] = postUploadCmdPrefix(**uploadArgs)

        if self.productName == 'xulrunner':
            self.addStep(RetryingMockProperty(
             command=self.makeCmd + ['-f', 'client.mk', 'upload'],
             env=uploadEnv,
             workdir='build',
             extract_fn = parse_make_upload,
             haltOnFailure=True,
             description=["upload"],
             timeout=60*60, # 60 minutes
             log_eval_func=lambda c,s: regex_log_evaluator(c, s, upload_errors),
             locks=[upload_lock.access('counting')],
             mock=self.use_mock,
             target=self.mock_target,
            ))
        else:
            objdir = WithProperties('%(basedir)s/' + self.baseWorkDir + '/' + self.objdir)
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
                timeout=40*60, # 40 minutes
                log_eval_func=lambda c,s: regex_log_evaluator(c, s, upload_errors),
                locks=[upload_lock.access('counting')],
            ))

        if self.profiledBuild:
            talosBranch = "%s-%s-pgo-talos" % (self.branchName, self.complete_platform)
        else:
            talosBranch = "%s-%s-talos" % (self.branchName, self.complete_platform)

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
            if '1.9.1' not in self.branchName:
                files.append(WithProperties('%(testsUrl)s'))

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
    def __init__(self, env, version, buildNumber, partialUpdates, brandName=None,
            unittestMasters=None, unittestBranch=None, talosMasters=None,
            usePrettyNames=True, enableUpdatePackaging=True, appVersion=None,
            **kwargs):
        self.version = version
        self.buildNumber = buildNumber
        self.partialUpdates = partialUpdates
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
        MercurialBuildFactory.__init__(self, env=env, **kwargs)

    def addFilePropertiesSteps(self, filename=None, directory=None,
                               fileType=None, maxDepth=1, haltOnFailure=False):
        # We don't need to do this for release builds.
        pass

    def addCreatePartialUpdateSteps(self):
        updateEnv = self.env.copy()
        # need to use absolute paths since mac may have difeerent layout
        # objdir/arch/dist vs objdir/dist
        updateEnv['MAR'] = WithProperties(
            '%(basedir)s' + '/%s/dist/host/bin/mar' % self.absMozillaObjDir)
        updateEnv['MBSDIFF'] = WithProperties(
            '%(basedir)s' + '/%s/dist/host/bin/mbsdiff' % self.absMozillaObjDir)
        update_dir = 'update/%s/en-US' % getPlatformFtpDir(self.platform)
        current_mar_name = '%s-%s.complete.mar' % (self.productName,
                                                   self.version)
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
                     '../dist/%s/%s' % (update_dir, current_mar_name)],
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
                protocol='http', server=self.stageServer)
            previousMarURL = '%s/update/%s/en-US/%s' % \
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
                command=['wget', '-O', 'previous.mar', '--no-check-certificate',
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
                        '%s/%s' % (update_dir, partial_mar_name),
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
                                '%s/%s' % (update_dir, partial_mar_name)],
                        workdir='%s/dist' % self.absMozillaObjDir,
                        haltOnFailure=True,
            ))
            self.UPLOAD_EXTRA_FILES.append('%s/%s' % (update_dir, partial_mar_name))
            if self.enableSigning and self.signingServers:
                partial_mar_path = '%s/dist/%s/%s' % \
                    (self.absMozillaObjDir, update_dir, partial_mar_name)
                cmd = '%s -f mar -f gpg "%s"' % (self.signing_command,
                                                partial_mar_path)
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
                self.UPLOAD_EXTRA_FILES.append('%s/%s.asc' % (update_dir,
                                                            partial_mar_name))

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
         timeout=60*60, # 60 minutes
         log_eval_func=lambda c,s: regex_log_evaluator(c, s, upload_errors),
         target=self.mock_target,
         mock=self.use_mock,
         mock_workdir_prefix=None,
        ))

        if self.productName == 'fennec' and not uploadMulti:
            cmd = ['scp']
            if self.stageSshKey:
                cmd.append('-oIdentityFile=~/.ssh/%s' % self.stageSshKey)
            cmd.append(info_txt)
            candidates_dir = makeCandidatesDir(self.productName, self.version,
                                               self.buildNumber)
            cmd.append('%s@%s:%s' % (self.stageUsername, self.stageServer,
                                     candidates_dir))
            self.addStep(RetryingShellCommand(
                name='upload_buildID',
                command=cmd,
                workdir='build/%s/dist' % self.mozillaObjdir
            ))

        # Send to the "release" branch on talos, it will do
        # super-duper-extra testing
        talosBranch = "release-%s-%s-talos" % (self.branchName, self.complete_platform)
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

class XulrunnerReleaseBuildFactory(ReleaseBuildFactory):

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_TO_TEMP': '1'})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        uploadEnv['POST_UPLOAD_CMD'] = 'post_upload.py ' + \
                                       '-p %s ' % self.productName + \
                                       '-v %s ' % self.version + \
                                       '-n %s ' % self.buildNumber + \
                                       '--release-to-candidates-dir'
        if self.signingServers and self.enableSigning:
            uploadEnv['POST_UPLOAD_CMD'] += ' --signed'

        def get_url(rc, stdout, stderr):
            for m in re.findall("^(http://.*?\.(?:tar\.bz2|dmg|zip))", "\n".join([stdout, stderr]), re.M):
                if m.endswith("crashreporter-symbols.zip"):
                    continue
                if m.endswith("tests.tar.bz2"):
                    continue
                return {'packageUrl': m}
            return {'packageUrl': ''}

        self.addStep(RetryingMockProperty(
         command=self.makeCmd + ['-f', 'client.mk', 'upload'],
         env=uploadEnv,
         workdir='build',
         extract_fn = get_url,
         haltOnFailure=True,
         description=['upload'],
         log_eval_func=lambda c,s: regex_log_evaluator(c, s, upload_errors),
         mock=self.use_mock,
         target=self.mock_target,
        ))


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


class BaseRepackFactory(MozillaBuildFactory):
    # Override ignore_dirs so that we don't delete l10n nightly builds
    # before running a l10n nightly build
    ignore_dirs = MozillaBuildFactory.ignore_dirs + [reallyShort('*-nightly')]

    extraConfigureArgs = []

    def __init__(self, project, appName, l10nRepoPath,
                 compareLocalesRepoPath, compareLocalesTag, stageServer,
                 stageUsername, stageSshKey=None, objdir='', platform='',
                 mozconfig=None,
                 tree="notset", mozillaDir=None, l10nTag='default',
                 mergeLocales=True,
                 testPrettyNames=False,
                 callClientPy=False,
                 clientPyConfig=None,
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

        self.addStep(SetBuildProperty(
         property_name='tree',
         value=self.tree,
         haltOnFailure=True
        ))

        self.origSrcDir = self.branchName

        # Mozilla subdir
        if mozillaDir:
            self.mozillaDir = '/%s' % mozillaDir
            self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, mozillaDir)
        else:
            self.mozillaDir = ''
            self.mozillaSrcDir = self.origSrcDir

        # self.mozillaObjdir is used in SeaMonkey's and Thunderbird's case
        self.objdir = objdir or self.origSrcDir
        self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)

        # These following variables are useful for sharing build steps (e.g.
        # update generation) from classes that use object dirs (e.g. nightly
        # repacks).
        #
        # We also concatenate the baseWorkDir at the outset to avoid having to
        # do that everywhere.
        self.absMozillaSrcDir = "%s/%s" % (self.baseWorkDir, self.mozillaSrcDir)
        self.absMozillaObjDir = '%s/%s' % (self.baseWorkDir, self.mozillaObjdir)

        self.latestDir = '/pub/mozilla.org/%s' % self.productName + \
                         '/nightly/latest-%s-l10n' % self.branchName

        if objdir != '':
            # L10NBASEDIR is relative to MOZ_OBJDIR
            self.env.update({'MOZ_OBJDIR': objdir,
                             'L10NBASEDIR':  '../../%s' % self.l10nRepoPath})

        if platform == 'macosx64':
            # use "mac" instead of "mac64" for macosx64
            self.env.update({'MOZ_PKG_PLATFORM': 'mac'})

        self.uploadEnv = self.env.copy() # pick up any env variables in our subclass
        self.uploadEnv.update({
            'AB_CD': WithProperties('%(locale)s'),
            'UPLOAD_HOST': stageServer,
            'UPLOAD_USER': stageUsername,
            'UPLOAD_TO_TEMP': '1',
            'POST_UPLOAD_CMD': self.postUploadCmd # defined in subclasses
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
         name='mkdir_l10nrepopath',
         command=['sh', '-c', 'mkdir -p %s' % self.l10nRepoPath],
         descriptionDone='mkdir '+ self.l10nRepoPath,
         workdir=self.baseWorkDir,
         flunkOnFailure=False
        ))

        # call out to overridable functions
        self.getSources()
        self.updateSources()
        self.getMozconfig()
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
        if not self.mozconfig:
            return

        cmd = ['bash', '-c',
                '''if [ -f "%(mozconfig)s" ]; then
                    echo Using in-tree mozconfig;
                    cp %(mozconfig)s .mozconfig;
                else
                    echo Could not find in-tree mozconfig;
                    exit 1;
                fi'''.replace("\n","") % {'mozconfig': self.mozconfig}]

        self.addStep(RetryingShellCommand(
            name='get_mozconfig',
            command=cmd,
            description=['getting', 'mozconfig'],
            descriptionDone=['got', 'mozconfig'],
            workdir='build/'+self.origSrcDir,
            haltOnFailure=True,
        ))
        self.addStep(ShellCommand(
            name='cat_mozconfig',
            command=['cat', '.mozconfig'],
            workdir='build/'+self.origSrcDir
        ))

    def configure(self):
        self.addStep(MockCommand(
         name='autoconf',
         command=['bash', '-c', 'autoconf-2.13'],
         haltOnFailure=True,
         descriptionDone=['autoconf'],
         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir),
         mock=self.use_mock,
         target=self.mock_target,
        ))
        if (self.mozillaDir):
            self.addStep(MockCommand(
             name='autoconf_mozilla',
             command=['bash', '-c', 'autoconf-2.13'],
             haltOnFailure=True,
             descriptionDone=['autoconf mozilla'],
             workdir='%s/%s' % (self.baseWorkDir, self.mozillaSrcDir),
             mock=self.use_mock,
             target=self.mock_target,
            ))
        self.addStep(MockCommand(
         name='autoconf_js_src',
         command=['bash', '-c', 'autoconf-2.13'],
         haltOnFailure=True,
         descriptionDone=['autoconf js/src'],
         workdir='%s/%s/js/src' % (self.baseWorkDir, self.mozillaSrcDir),
         mock=self.use_mock,
         target=self.mock_target,
        ))
        # WinCE is the only platform that will do repackages with
        # a mozconfig for now. This will be fixed in bug 518359
        # For backward compatibility where there is no mozconfig
        self.addStep(MockCommand(**self.processCommand(
         name='configure',
         command=['sh', '--',
                  './configure', '--enable-application=%s' % self.appName,
                  '--with-l10n-base=../%s' % self.l10nRepoPath ] +
                  self.extraConfigureArgs,
         description='configure',
         descriptionDone='configure done',
         env=self.env,
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir),
         mock=self.use_mock,
         target=self.mock_target,
        )))
        self.addStep(MockCommand(**self.processCommand(
         name='make_config',
         command=self.makeCmd,
         workdir='%s/%s/config' % (self.baseWorkDir, self.mozillaObjdir),
         description=['make config'],
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
        self.tinderboxPrint('locale',WithProperties('%(locale)s'))
        self.tinderboxPrint('tree',self.tree)
        self.tinderboxPrint('buildnumber',WithProperties('%(buildnumber)s'))

    def doUpload(self, postUploadBuildDir=None, uploadMulti=False):
        self.addStep(RetryingMockProperty(
         name='make_upload',
         command=self.makeCmd + ['upload', WithProperties('AB_CD=%(locale)s')],
         env=self.uploadEnv,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir,
                                               self.appName),
         haltOnFailure=True,
         flunkOnFailure=True,
         log_eval_func=lambda c,s: regex_log_evaluator(c, s, upload_errors),
         locks=[upload_lock.access('counting')],
         extract_fn=parse_make_upload,
         mock=self.use_mock,
         target=self.mock_target,
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

        mirrors=[]
        if self.baseMirrorUrls:
            mirrors = [WithProperties(url + "/" + self.l10nRepoPath + "/%(locale)s") for url in self.baseMirrorUrls]
        step = self.makeHgtoolStep(
                name='get_locale_src',
                rev=WithProperties("%(l10n_revision)s"),
                repo_url=WithProperties("http://" + self.hgHost + "/" + \
                                 self.l10nRepoPath + "/%(locale)s"),
                workdir='%s/%s' % (self.baseWorkDir, self.l10nRepoPath),
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
            co_command.append('--mozilla-repo=%s' % self.getRepository(c.get('moz_repo_path')))
        if c.get('inspector_repo_path'):
            co_command.append('--inspector-repo=%s' % self.getRepository(c.get('inspector_repo_path')))
        elif skipBlankRepos:
            co_command.append('--skip-inspector')
        if c.get('venkman_repo_path'):
            co_command.append('--venkman-repo=%s' % self.getRepository(c.get('venkman_repo_path')))
        elif skipBlankRepos:
            co_command.append('--skip-venkman')
        if c.get('chatzilla_repo_path'):
            co_command.append('--chatzilla-repo=%s' % self.getRepository(c.get('chatzilla_repo_path')))
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
         timeout=60*60 # 1 hour
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
         command=['hg', 'clone', compareLocalesRepo, 'compare-locales'],
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
            mergeLocaleOptions = ['-m', 'merged']
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
         workdir="%s/%s/%s/locales" % (self.baseWorkDir,
                                       self.origSrcDir,
                                       self.appName),
         haltOnFailure=True
        ))
        self.addStep(ShellCommand(
         name='run_compare_locales',
         command=['python',
                  '../../../compare-locales/scripts/compare-locales'] +
                  mergeLocaleOptions +
                  ["l10n.ini",
                  "../../../%s" % self.l10nRepoPath,
                  WithProperties('%(locale)s')],
         description='comparing locale',
         env={'PYTHONPATH': ['../../../compare-locales/lib']},
         flunkOnFailure=flunkOnFailure,
         warnOnFailure=warnOnFailure,
         haltOnFailure=haltOnFailure,
         workdir="%s/%s/%s/locales" % (self.baseWorkDir,
                                       self.origSrcDir,
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
                  'if [ -d '+self.mozillaObjdir+'/dist/upload ]; then ' +
                  'rm -rf '+self.mozillaObjdir+'/dist/upload; ' +
                  'fi'],
         description="rm dist/upload",
         workdir=self.baseWorkDir,
         haltOnFailure=True
        ))

        self.addStep(ShellCommand(
         name='rm_dist_update',
         command=['sh', '-c',
                  'if [ -d '+self.mozillaObjdir+'/dist/update ]; then ' +
                  'rm -rf '+self.mozillaObjdir+'/dist/update; ' +
                  'fi'],
         description="rm dist/update",
         workdir=self.baseWorkDir,
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
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
         mock=self.use_mock,
         target=self.mock_target,
        ))
        self.addStep(MockCommand(
         name='make_unpack',
         command=self.makeCmd + ['unpack'],
         description='unpack en-US',
         haltOnFailure=True,
         env=self.env,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
         mock=self.use_mock,
         target=self.mock_target,
        ))
        # We need to override ZIP_IN because it defaults to $(PACKAGE), which
        # will be the pretty name version here.
        self.addStep(MockProperty(
         command=self.makeCmd + ['--no-print-directory', 'echo-variable-ZIP_IN'],
         property='zip_in',
         env=self.env,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
         haltOnFailure=True,
         mock=self.use_mock,
         target=self.mock_target,
        ))
        prettyEnv = self.env.copy()
        prettyEnv['MOZ_PKG_PRETTYNAMES'] = '1'
        prettyEnv['ZIP_IN'] = WithProperties('%(zip_in)s')
        if self.platform.startswith('win'):
            self.addStep(MockProperty(
             command=self.makeCmd + ['--no-print-directory', 'echo-variable-WIN32_INSTALLER_IN'],
             property='win32_installer_in',
             env=self.env,
             workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
             haltOnFailure=True,
             mock=self.use_mock,
             target=self.mock_target,
            ))
            prettyEnv['WIN32_INSTALLER_IN'] = WithProperties('%(win32_installer_in)s')
        self.addStep(MockCommand(
         name='repack_installers_pretty',
         description=['repack', 'installers', 'pretty'],
         command=['sh', '-c',
            WithProperties('make installers-%(locale)s LOCALE_MERGEDIR=$PWD/merged')],
         env=prettyEnv,
         haltOnFailure=False,
         flunkOnFailure=False,
         warnOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
         mock=self.use_mock,
         target=self.mock_target,
        ))

class NightlyRepackFactory(BaseRepackFactory, NightlyBuildFactory):
    extraConfigureArgs = []

    def __init__(self, enUSBinaryURL, nightly=False, env={},
                 ausBaseUploadDir=None, updatePlatform=None,
                 downloadBaseURL=None, ausUser=None, ausSshKey=None,
                 ausHost=None, l10nNightlyUpdate=False, l10nDatedDirs=False,
                 createPartial=False, extraConfigureArgs=[], **kwargs):
        self.nightly = nightly
        self.l10nNightlyUpdate = l10nNightlyUpdate
        self.ausBaseUploadDir = ausBaseUploadDir
        self.updatePlatform = updatePlatform
        self.downloadBaseURL = downloadBaseURL
        self.ausUser = ausUser
        self.ausSshKey = ausSshKey
        self.ausHost = ausHost
        self.createPartial = createPartial
        self.geriatricMasters = []
        self.extraConfigureArgs = extraConfigureArgs

        # This is required because this __init__ doesn't call the
        # NightlyBuildFactory __init__ where self.complete_platform
        # is set.  This is only used for android, which doesn't
        # use this factory, so is safe
        self.complete_platform = ''

        env = env.copy()

        env.update({'EN_US_BINARY_URL':enUSBinaryURL})

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
            if not '--enable-update-packaging' in self.extraConfigureArgs:
                self.extraConfigureArgs += ['--enable-update-packaging']

        BaseRepackFactory.__init__(self, env=env, **kwargs)

        if l10nNightlyUpdate:
            assert ausBaseUploadDir and updatePlatform and downloadBaseURL
            assert ausUser and ausSshKey and ausHost

            # To preserve existing behavior, we need to set the
            # ausFullUploadDir differently for when we are create all the
            # mars (complete+partial) ourselves.
            if self.createPartial:
                # e.g.:
                # /opt/aus2/incoming/2/Firefox/mozilla-central/WINNT_x86-msvc
                self.ausFullUploadDir = '%s/%s' % (self.ausBaseUploadDir,
                                                   self.updatePlatform)
            else:
                # this is a tad ugly because we need python interpolation
                # as well as WithProperties, e.g.:
                # /opt/aus2/build/0/Firefox/mozilla-central/WINNT_x86-msvc/2008010103/en-US
                self.ausFullUploadDir = '%s/%s/%%(buildid)s/%%(locale)s' % \
                  (self.ausBaseUploadDir, self.updatePlatform)
            NightlyBuildFactory.addCreateSnippetsSteps(self,
                                                       milestone_extra='-l10n')
            NightlyBuildFactory.addUploadSnippetsSteps(self)
            NightlyBuildFactory.addSubmitBalrogUpdates(self)

        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def getPreviousBuildUploadDir(self):
        if self.createPartial:
            return "%s/%%(previous_buildid)s/%%(locale)s" % \
                                         self.ausFullUploadDir
        else:
            return self.ausFullUploadDir

    def getCurrentBuildUploadDir(self):
        if self.createPartial:
            return "%s/%%(buildid)s/%%(locale)s" % self.ausFullUploadDir
        else:
            return self.ausFullUploadDir

    def updateSources(self):
        self.addStep(ShellCommand(
         name='update_locale_source',
         command=['hg', 'up', '-C', '-r', self.l10nTag],
         description='update workdir',
         workdir=WithProperties('build/' + self.l10nRepoPath + '/%(locale)s'),
         haltOnFailure=True
        ))
        self.addStep(SetProperty(
                     command=['hg', 'ident', '-i'],
                     haltOnFailure=True,
                     property='l10n_revision',
                     workdir=WithProperties('build/' + self.l10nRepoPath +
                                            '/%(locale)s')
        ))

    def downloadBuilds(self):
        self.addStep(RetryingMockCommand(
         name='wget_enUS',
         command=self.makeCmd + ['wget-en-US'],
         descriptionDone='wget en-US',
         env=self.env,
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
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
                     workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
                     mock=self.use_mock,
                     target=self.mock_target,
                     ))
        self.addStep(MockProperty(
                     command=self.makeCmd + ['ident'],
                     haltOnFailure=True,
                     workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
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
                         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir),
                         mock=self.use_mock,
                         target=self.mock_target,
                        ))
            self.addStep(MockCommand(
                         name='update_mozilla_enUS_revision',
                         command=['hg', 'update', '-C', '-r',
                                  WithProperties('%(moz_revision)s')],
                         haltOnFailure=True,
                         env=self.env,
                         workdir='%s/%s' % (self.baseWorkDir, self.mozillaSrcDir),
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
                         workdir='build/' + self.origSrcDir,
                         mock=self.use_mock,
                         target=self.mock_target,
                        ))

    def tinderboxPrintRevisions(self):
        if self.clientPyConfig:
            self.tinderboxPrint('comm_revision',WithProperties('%(comm_revision)s'))
            self.tinderboxPrint('moz_revision',WithProperties('%(moz_revision)s'))
            self.tinderboxPrint('l10n_revision',WithProperties('%(l10n_revision)s'))
        else:
            self.tinderboxPrint('fx_revision',WithProperties('%(fx_revision)s'))
            self.tinderboxPrint('l10n_revision',WithProperties('%(l10n_revision)s'))

    def makePartialTools(self):
        # Build the tools we need for update-packaging, specifically bsdiff.
        # Configure can take a while.
        self.addStep(MockCommand(
            name='make_bsdiff',
            command=['sh', '-c',
                     'if [ ! -e dist/host/bin/mbsdiff ]; then ' +
                     'make tier_base; make tier_nspr; make -C config;' +
                     'make -C modules/libmar; make -C modules/libbz2;' +
                     'make -C other-licenses/bsdiff;'
                     'fi'],
            description=['make', 'bsdiff'],
            workdir=self.absMozillaObjDir,
            haltOnFailure=True,
            mock=self.use_mock,
            target=self.mock_target,
        ))

    # The parent class gets us most of the way there, we just need to add the
    # locale.
    def getCompleteMarPatternMatch(self):
        return '.%(locale)s.' + NightlyBuildFactory.getCompleteMarPatternMatch(self)

    def doRepack(self):
        self.addStep(MockCommand(
         name='make_tier_base',
         command=self.makeCmd + ['tier_base'],
         workdir='%s/%s' % (self.baseWorkDir, self.mozillaObjdir),
         description=['make tier_base'],
         haltOnFailure=True,
         mock=self.use_mock,
         target=self.mock_target,
        ))
        self.addStep(MockCommand(
         name='make_tier_nspr',
         command=self.makeCmd + ['tier_nspr'],
         workdir='%s/%s' % (self.baseWorkDir, self.mozillaObjdir),
         description=['make tier_nspr'],
         haltOnFailure=True,
         mock=self.use_mock,
         target=self.mock_target,
        ))
        if self.l10nNightlyUpdate:
            # Because we're generating updates we need to build the libmar tools
            self.addStep(MockCommand(
             name='make_libmar',
             command=self.makeCmd,
             workdir='%s/%s/modules/libmar' % (self.baseWorkDir, self.mozillaObjdir),
             description=self.makeCmd + ['modules/libmar'],
             haltOnFailure=True,
             mock=self.use_mock,
             target=self.mock_target,
            ))
        self.addStep(MockCommand(
         name='repack_installers',
         description=['repack', 'installers'],
         command=['sh', '-c',
            WithProperties('make installers-%(locale)s LOCALE_MERGEDIR=$PWD/merged')],
         env = self.env,
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.objdir, self.appName),
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
        self.addStep(SetProperty(
            command=['python', 'config/printconfigsetting.py',
                     WithProperties('%(inipath)s'),
                     'App', 'BuildID'],
            name='get_build_id',
            workdir=self.absMozillaSrcDir,
            property='buildid',
        ))
        if self.l10nNightlyUpdate:
            # We need the appVersion to create snippets
            self.addStep(SetProperty(
                command=['python', 'config/printconfigsetting.py',
                         WithProperties('%(inipath)s'),
                         'App', 'Version'],
                property='appVersion',
                name='get_app_version',
                workdir=self.absMozillaSrcDir,
            ))
            self.addStep(SetProperty(
                command=['python', 'config/printconfigsetting.py',
                         WithProperties('%(inipath)s'),
                         'App', 'Name'],
                property='appName',
                name='get_app_name',
                workdir=self.absMozillaSrcDir,
            ))
            self.addFilePropertiesSteps(filename='*.complete.mar',
                                        directory='%s/dist/update' % self.absMozillaSrcDir,
                                        fileType='completeMar',
                                        haltOnFailure=True)

        # Remove the source (en-US) package so as not to confuse later steps
        # that look up build details.
        self.addStep(ShellCommand(name='rm_en-US_build',
                                  command=['bash', '-c', 'rm -rf *.en-US.*'],
                                  description=['remove','en-US','build'],
                                  env=self.env,
                                  workdir='%s/dist' % self.absMozillaObjDir,
                                  haltOnFailure=True)
         )
        if self.l10nNightlyUpdate and self.createPartial:
            self.addCreatePartialUpdateSteps(extraArgs=[WithProperties('AB_CD=%(locale)s')])


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

class StagingRepositorySetupFactory(ReleaseFactory):
    """This Factory should be run at the start of a staging release run. It
       deletes and reclones all of the repositories in 'repositories'. Note that
       the staging buildTools repository should _not_ be recloned, as it is
       used by many other builders, too.
    """
    def __init__(self, username, sshKey, repositories, userRepoRoot,
                 **kwargs):
        # MozillaBuildFactory needs the 'repoPath' argument, but we don't
        ReleaseFactory.__init__(self, repoPath='nothing', **kwargs)
        for repoPath in sorted(repositories.keys()):
            repo = self.getRepository(repoPath)
            repoName = self.getRepoName(repoPath)
            # Don't use cache for user repos
            rnd = random.randint(100000, 999999)
            userRepoURL = '%s/%s?rnd=%s' % (self.getRepository(userRepoRoot),
                                        repoName, rnd)

            # test for existence
            command = 'wget -O /dev/null %s' % repo
            command += ' && { '
            command += 'if wget -q -O /dev/null %s; then ' % userRepoURL
            # if it exists, delete it
            command += 'echo "Deleting %s"; sleep 2; ' % repoName
            command += 'ssh -l %s -i %s %s edit %s delete YES; ' % \
              (username, sshKey, self.hgHost, repoName)
            command += 'else echo "Not deleting %s"; exit 0; fi }' % repoName

            self.addStep(MockCommand(
             name='delete_repo',
             command=['bash', '-c', command],
             description=['delete', repoName],
             haltOnFailure=True,
             timeout=30*60, # 30 minutes
             mock=self.use_mock,
             target=self.mock_target,
             workdir=WithProperties('%(basedir)s'),
             mock_workdir_prefix=None,
             env=self.env,
            ))

        # Wait for hg.m.o to catch up
        self.addStep(ShellCommand(
         name='wait_for_hg',
         command=['sleep', '600'],
         description=['wait', 'for', 'hg'],
        ))

        for repoPath in sorted(repositories.keys()):
            repo = self.getRepository(repoPath)
            repoName = self.getRepoName(repoPath)
            timeout = 60*60
            command = ['python',
                       WithProperties('%(toolsdir)s/buildfarm/utils/retry.py'),
                       '--timeout', timeout,
                       'ssh', '-l', username, '-oIdentityFile=%s' % sshKey,
                       self.hgHost, 'clone', repoName, repoPath]

            self.addStep(MockCommand(
             name='recreate_repo',
             command=command,
             description=['recreate', repoName],
             timeout=timeout,
             mock=self.use_mock,
             target=self.mock_target,
             workdir=WithProperties('%(basedir)s'),
             mock_workdir_prefix=None,
            ))

        # Wait for hg.m.o to catch up
        self.addStep(ShellCommand(
         name='wait_for_hg',
         command=['sleep', '600'],
         description=['wait', 'for', 'hg'],
        ))


class SingleSourceFactory(ReleaseFactory):
    def __init__(self, productName, version, baseTag, stagingServer,
                 stageUsername, stageSshKey, buildNumber, mozconfig,
                 configRepoPath, configSubDir, objdir='',
                 mozillaDir=None, autoconfDirs=['.'], buildSpace=1,
                 mozconfigBranch="production", appVersion=None, **kwargs):
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)

        self.mozconfig = mozconfig
        self.configRepoPath=configRepoPath
        self.configSubDir=configSubDir
        self.mozconfigBranch = mozconfigBranch
        self.releaseTag = '%s_RELEASE' % (baseTag)

        self.origSrcDir = self.branchName

        # Mozilla subdir
        if mozillaDir:
            self.mozillaDir = '/%s' % mozillaDir
            self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, mozillaDir)
        else:
            self.mozillaDir = ''
            self.mozillaSrcDir = self.origSrcDir

        # self.mozillaObjdir is used in SeaMonkey's and Thunderbird's case
        self.objdir = objdir or self.origSrcDir
        self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)
        self.distDir = "%s/dist" % self.mozillaObjdir

        # Make sure MOZ_PKG_PRETTYNAMES is set so that our source package is
        # created in the expected place.
        self.env['MOZ_OBJDIR'] = self.objdir
        self.env['MOZ_PKG_PRETTYNAMES'] = '1'
        if appVersion is None or version != appVersion or \
           (self.branchName == 'mozilla-1.9.2' and productName == 'xulrunner'):
            self.env['MOZ_PKG_VERSION'] = version
        self.env['MOZ_PKG_APPNAME'] = productName

        # '-c' is for "release to candidates dir"
        postUploadCmd = 'post_upload.py -p %s -v %s -n %s -c' % \
          (productName, version, buildNumber)
        if productName == 'fennec':
            postUploadCmd = 'post_upload.py -p mobile --nightly-dir candidates -v %s -n %s -c' % \
                          (version, buildNumber)
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
            workdir="%s/%s" % (self.mozillaSrcDir, self.mozillaObjdir),
            env=self.env,
            description=['make source-package'],
            mock=self.use_mock,
            target=self.mock_target,
            haltOnFailure=True,
            timeout=60*60 # 60 minutes
        ))
        self.addStep(RetryingMockCommand(
            name='upload_files',
            command=self.makeCmd + ['source-upload', 'UPLOAD_HG_BUNDLE=1'],
            workdir="%s/%s" % (self.mozillaSrcDir, self.mozillaObjdir),
            env=uploadEnv,
            description=['upload files'],
            mock=self.use_mock,
            target=self.mock_target,
        ))

    def addConfigSteps(self, workdir='build'):
        assert self.configRepoPath is not None
        assert self.configSubDir is not None
        assert self.mozconfig is not None
        configRepo = self.getRepository(self.configRepoPath)

        self.mozconfig = 'configs/%s/%s/mozconfig' % (self.configSubDir,
                                                      self.mozconfig)
        self.addStep(ShellCommand(
                     name='rm_configs',
                     command=['rm', '-rf', 'configs'],
                     description=['removing', 'configs'],
                     descriptionDone=['remove', 'configs'],
                     haltOnFailure=True,
                     workdir='.'
        ))
        self.addStep(MercurialCloneCommand(
                     name='hg_clone_configs',
                     command=['hg', 'clone', configRepo, 'configs'],
                     description=['checking', 'out', 'configs'],
                     descriptionDone=['checkout', 'configs'],
                     haltOnFailure=True,
                     workdir='.'
        ))
        self.addStep(ShellCommand(
                     name='hg_update',
                     command=['hg', 'update', '-r', self.mozconfigBranch],
                     description=['updating', 'mozconfigs'],
                     haltOnFailure=True,
                     workdir='./configs'
        ))
        self.addStep(ShellCommand(
                     # cp configs/mozilla2/$platform/$repo/$type/mozconfig .mozconfig
                     name='cp_mozconfig',
                     command=['cp', self.mozconfig, '%s/.mozconfig' % workdir],
                     description=['copying', 'mozconfig'],
                     descriptionDone=['copy', 'mozconfig'],
                     haltOnFailure=True,
                     workdir='.'
        ))
        self.addStep(ShellCommand(
                     name='cat_mozconfig',
                     command=['cat', '.mozconfig'],
                     workdir=workdir
        ))


class ReleaseUpdatesFactory(ReleaseFactory):
    snippetStagingDir = '/opt/aus2/snippets/staging'
    def __init__(self, patcherConfig, verifyConfigs,
                 appName, productName, configRepoPath,
                 version, appVersion, baseTag, buildNumber,
                 partialUpdates,
                 ftpServer, bouncerServer, stagingServer,
                 stageUsername, stageSshKey, ausUser, ausSshKey, ausHost,
                 ausServerUrl, hgSshKey, hgUsername, releaseChannel='release',
                 mozRepoPath=None,
                 brandName=None, buildSpace=2, triggerSchedulers=None,
                 releaseNotesUrl=None, python='python',
                 testOlderPartials=False,
                 longVersion=None, schema=None,
                 useBetaChannelForRelease=False, useChecksums=False, **kwargs):
        """patcherConfig: The filename of the patcher config file to bump,
                          and pass to patcher.
           mozRepoPath: The path for the Mozilla repo to hand patcher as the
                        HGROOT (if omitted, the default repoPath is used).
                        Apps not rooted in the Mozilla repo need this.
           brandName: The brand name as used on the updates server. If omitted,
                      the first letter of the brand name is uppercased.
           schema: The style of snippets to write (changed in bug 459972)
        """
        if useChecksums:
            buildSpace = 1
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
        self.stagingServer = stagingServer
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey
        self.ausUser = ausUser
        self.ausSshKey = ausSshKey
        self.ausHost = ausHost
        self.ausServerUrl = ausServerUrl
        self.hgSshKey = hgSshKey
        self.hgUsername = hgUsername
        self.releaseChannel = releaseChannel
        self.triggerSchedulers = triggerSchedulers
        self.testOlderPartials = testOlderPartials
        self.longVersion = longVersion or self.version
        self.schema = schema
        self.useBetaChannelForRelease = useBetaChannelForRelease
        self.useChecksums = useChecksums
        self.python = python
        self.configRepoPath = configRepoPath

        # The patcher config bumper needs to know the exact previous version
        self.previousVersion = str(max(LooseVersion(v) for v in self.partialUpdates))
        self.previousAppVersion = self.partialUpdates[self.previousVersion].get('appVersion', self.previousVersion)
        self.previousBuildNumber = self.partialUpdates[self.previousVersion]['buildNumber']
        self.patcherConfigFile = 'tools/release/patcher-configs/%s' % patcherConfig
        self.shippedLocales = self.getShippedLocales(self.repository, baseTag,
                                                appName)
        self.candidatesDir = self.getCandidatesDir(productName, version, buildNumber)

        if mozRepoPath:
          self.mozRepository = self.getRepository(mozRepoPath)
        else:
          self.mozRepository = self.repository


        self.brandName = brandName or productName.capitalize()
        self.releaseNotesUrl = releaseNotesUrl

        self.setChannelData()
        self.bumpConfigs()
        self.createPatches()
        if buildNumber >= 2:
            self.createBuildNSnippets()
        self.uploadSnippets()
        self.verifySnippets()
        self.trigger()

    def setChannelData(self):
        # This method figures out all the information needed to push snippets
        # to AUS, push test snippets live, and do basic verifications on them.
        # Test snippets and whatever channel self.relesaeChannel is always end
        # up in the same local and remote directories.
        # When useBetaChannelForRelease is True, we have a 'beta' channel
        # in addition to whatever the releaseChannel.
        baseSnippetDir = self.getSnippetDir()
        self.dirMap = {
            'aus2.test': '%s-test' % baseSnippetDir,
            'aus2': baseSnippetDir
        }

        self.channels = {
            'betatest': { 'dir': 'aus2.test' },
            'releasetest': { 'dir': 'aus2.test' },
            self.releaseChannel: {
                'dir': 'aus2',
                'compareTo': 'releasetest',
            }
        }

        if self.useBetaChannelForRelease:
            self.dirMap['aus2.beta'] = '%s-beta' % baseSnippetDir
            self.channels['beta'] = {'dir': 'aus2.beta'}

        # XXX: hack alert
        if 'esr' in self.version:
            self.testChannel = 'esrtest'
            self.channels['esrtest'] = { 'dir': 'aus2.test' }
        else:
            self.testChannel = 'betatest'

    def bumpConfigs(self):
        self.addStep(RetryingMockCommand(
         name='get_shipped_locales',
         command=['wget', '-O', 'shipped-locales', self.shippedLocales],
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
                       '-t', self.stagingServer, '-f', self.ftpServer,
                       '-d', self.bouncerServer, '-l', 'shipped-locales']
        for previousVersion in self.partialUpdates:
            bumpCommand.extend(['--partial-version', previousVersion])
        for platform in sorted(self.verifyConfigs.keys()):
            bumpCommand.extend(['--platform', platform])
        if self.useBetaChannelForRelease:
            bumpCommand.append('-u')
        if self.releaseNotesUrl:
            bumpCommand.extend(['-n', self.releaseNotesUrl])
        if self.schema:
            bumpCommand.extend(['-s', str(self.schema)])
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
        tags = [release.info.getRuntimeTag(t) for t in release.info.getTags(self.baseTag, self.buildNumber)]
        releaseTag = release.info.getReleaseTag(self.baseTag)

        for platform, cfg in self.verifyConfigs.items():
            command = [self.python, 'tools/scripts/updates/create-update-verify-configs.py',
                       '-c', WithProperties(self.patcherConfigFile),
                       '--platform', platform,
                       '--output', 'tools/release/updates/' + cfg,
                       '--release-config-file', WithProperties('%(release_config)s'),
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
         ignoreCodes=[0,1]
        ))
        self.addStep(MockCommand(
         name='commit_configs',
         command=['hg', 'commit', '-u', self.hgUsername, '-m',
                  'Automated configuration bump: update configs ' + \
                  'for %s %s build %s' % (self.brandName, self.version,
                                          self.buildNumber)
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

    def createPatches(self):
        command=[self.python, 'tools/scripts/updates/create-snippets.py',
                 '--config', WithProperties(self.patcherConfigFile),
                 # XXX: should put this outside of the build dir somewhere
                 '--checksums-dir', 'checksums',
                 '--snippet-dir', 'aus2',
                 '--test-snippet-dir', 'aus2.test',
                 '-v']
        snippet_env = self.env.copy()
        snippet_env['PYTHONPATH'] = 'tools/lib/python:tools/lib/python/vendor'
        self.addStep(MockCommand(
         name='create_snippets',
         command=command,
         env=snippet_env,
         description=['create', 'snippets'],
         workdir='.',
         mock=self.use_mock,
         target=self.mock_target,
         haltOnFailure=True
        ))

    def createBuildNSnippets(self):
        command = [self.python,
                   WithProperties('%(toolsdir)s/release/generate-candidate-build-updates.py'),
                   '--brand', self.brandName,
                   '--product', self.productName,
                   '--app-name', self.appName,
                   '--version', self.version,
                   '--app-version', self.appVersion,
                   # XXX: needs to be updated for partialUpdates
                   '--old-version', self.previousVersion,
                   '--old-app-version', self.previousAppVersion,
                   '--build-number', self.buildNumber,
                   '--old-build-number', self.previousBuildNumber,
                   '--channel', 'betatest', '--channel', 'releasetest',
                   '--channel', self.releaseChannel,
                   '--stage-server', self.stagingServer,
                   '--old-base-snippet-dir', '.',
                   '--workdir', '.',
                   '--hg-server', self.getRepository('/'),
                   '--source-repo', self.repoPath,
                   '--verbose']
        for p in (self.verifyConfigs.keys()):
            command.extend(['--platform', p])
        if self.useBetaChannelForRelease:
            command.extend(['--channel', 'beta'])
        if self.testOlderPartials:
            command.extend(['--generate-partials'])
        snippet_env = self.env.copy()
        snippet_env['PYTHONPATH'] = WithProperties('%(toolsdir)s/lib/python')
        self.addStep(MockCommand(
         name='create_buildN_snippets',
         command=command,
         description=['generate snippets', 'for prior',
                      '%s builds' % self.version],
         env=snippet_env,
         haltOnFailure=False,
         flunkOnFailure=False,
         mock=self.use_mock,
         target=self.mock_target,
         workdir='.'
        ))

    def uploadSnippets(self):
        for localDir,remoteDir in self.dirMap.iteritems():
            snippetDir = self.snippetStagingDir + '/' + remoteDir
            self.addStep(RetryingShellCommand(
             name='upload_snippets',
             command=['rsync', '-av',
                      '-e', 'ssh -oIdentityFile=~/.ssh/%s' % self.ausSshKey,
                      localDir + '/',
                      '%s@%s:%s' % (self.ausUser, self.ausHost, snippetDir)],
             workdir='.',
             description=['upload', '%s snippets' % localDir],
             haltOnFailure=True
            ))
            # We only push test channel snippets from automation.
            if localDir.endswith('test'):
                self.addStep(RetryingShellCommand(
                 name='pushsnip',
                 command=['ssh', '-t', '-l', self.ausUser,
                          '-oIdentityFile=~/.ssh/%s' % self.ausSshKey,
                          self.ausHost, '~/bin/pushsnip %s' % remoteDir],
                 timeout=7200, # 2 hours
                 description=['pushsnip'],
                 haltOnFailure=True
                ))

    def verifySnippets(self):
        channelComparisons = [(c, self.channels[c]['compareTo']) for c in self.channels if 'compareTo' in self.channels[c]]
        for chan1,chan2 in channelComparisons:
            self.addStep(SnippetComparison(
                chan1=chan1,
                chan2=chan2,
                dir1=self.channels[chan1]['dir'],
                dir2=self.channels[chan2]['dir'],
                workdir='.'
            ))

    def trigger(self):
        if self.triggerSchedulers:
            self.addStep(Trigger(
             schedulerNames=self.triggerSchedulers,
             waitForFinish=False
            ))

    def getSnippetDir(self):
        return build.paths.getSnippetDir(self.brandName, self.version,
                                          self.buildNumber)


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
         workdir='tools/release'
        ))

class TuxedoEntrySubmitterFactory(ReleaseFactory):
    def __init__(self, baseTag, appName, config, productName, version,
                 tuxedoServerUrl, enUSPlatforms, l10nPlatforms,
                 extraPlatforms=None, bouncerProductName=None, brandName=None,
                 partialUpdates=None, credentialsFile=None, verbose=True,
                 dryRun=False, milestone=None, bouncerProductSuffix=None,
                 **kwargs):
        ReleaseFactory.__init__(self, **kwargs)

        extraPlatforms = extraPlatforms or []
        cmd = ['python', 'tuxedo-add.py',
               '--config', config,
               '--product', productName,
               '--version', version,
               '--tuxedo-server-url', tuxedoServerUrl]

        if l10nPlatforms:
            cmd.extend(['--shipped-locales', 'shipped-locales'])
            shippedLocales = self.getShippedLocales(self.repository, baseTag,
                                                    appName)
            self.addStep(ShellCommand(
             name='get_shipped_locales',
             command=['wget', '-O', 'shipped-locales', shippedLocales],
             description=['get', 'shipped-locales'],
             haltOnFailure=True,
             workdir='tools/release'
            ))

        bouncerProductName = bouncerProductName or productName.capitalize()
        cmd.extend(['--bouncer-product-name', bouncerProductName])
        brandName = brandName or productName.capitalize()
        cmd.extend(['--brand-name', brandName])

        for previousVersion in partialUpdates:
            cmd.extend(['--partial-version', previousVersion])

        if milestone:
            cmd.extend(['--milestone', milestone])

        if bouncerProductSuffix:
            cmd.extend(['--bouncer-product-suffix', bouncerProductSuffix])

        for platform in sorted(enUSPlatforms):
            cmd.extend(['--platform', platform])

        for platform in sorted(extraPlatforms):
            cmd.extend(['--platform', platform])

        if credentialsFile:
            target_file_name = os.path.basename(credentialsFile)
            cmd.extend(['--credentials-file', target_file_name])
            self.addStep(FileDownload(
             mastersrc=credentialsFile,
             slavedest=target_file_name,
             workdir='tools/release',
            ))

        self.addStep(RetryingShellCommand(
         name='tuxedo_add',
         command=cmd,
         description=['tuxedo-add.py'],
         env={'PYTHONPATH': ['../lib/python']},
         workdir='tools/release',
        ))

def parse_sendchange_files(build, include_substr='', exclude_substrs=[]):
    '''Given a build object, figure out which files have the include_substr
    in them, then exclude files that have one of the exclude_substrs. This
    function uses substring pattern matching instead of regular expressions
    as it meets the need without incurring as much overhead.'''
    potential_files=[]
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
                 resetHwClock=False, **kwargs):
        #Note: the posixBinarySuffix is needed because some products (firefox)
        #use 'firefox-bin' and some (fennec) use 'fennec' for the name of the
        #actual application binary.  This is only applicable to posix-like
        #systems.  Windows always uses productName.exe (firefox.exe and
        #fennec.exe)
        self.platform = platform.split('-')[0]
        self.productName = productName
        if not posixBinarySuffix:
            #all forms of no should result in empty string
            self.posixBinarySuffix = ''
        else:
            self.posixBinarySuffix = posixBinarySuffix
        self.downloadSymbols = downloadSymbols
        self.downloadSymbolsOnDemand = downloadSymbolsOnDemand
        self.downloadTests = downloadTests
        self.resetHwClock = resetHwClock

        assert self.platform in getSupportedPlatforms()

        MozillaBuildFactory.__init__(self, **kwargs)

        self.ignoreCerts = False
        if self.branchName.lower().startswith('shadow'):
            self.ignoreCerts = True

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
        #On windows, we should try using cmd's attrib and native rmdir
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
            assert len(build.source.changes[-1].files) > 0, 'Unittest sendchange has no files'
            return parse_sendchange_files(build, exclude_substrs=['.crashreporter-symbols.',
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
             value="%s/%s.exe" % (self.productName, self.productName),
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
                  WithProperties('%(exedir)s')],
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
                command=['bash', '-c', 'rm -rf Library/Saved\ Application\ State/*.savedState']
            ))
        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            #This step is to deal with minis running linux that don't reboot properly
            #see bug561442
            if self.resetHwClock and 'linux' in self.platform:
                self.addStep(ShellCommand(
                    name='set_time',
                    description=['set', 'time'],
                    alwaysRun=True,
                    command=['bash', '-c',
                             'sudo hwclock --set --date="$(date +%m/%d/%y\ %H:%M:%S)"'],
                ))
            self.addPeriodicRebootSteps()


def resolution_step():
    return ShellCommand(
        name='show_resolution',
        flunkOnFailure=False,
        warnOnFailure=False,
        haltOnFailure=False,
        workdir='/Users/cltbld',
        command=['bash', '-c', 'screenresolution get && screenresolution list && system_profiler SPDisplaysDataType']
    )

class UnittestPackagedBuildFactory(MozillaTestFactory):
    def __init__(self, platform, test_suites, env, productName='firefox',
                 mochitest_leak_threshold=None,
                 crashtest_leak_threshold=None, totalChunks=None,
                 thisChunk=None, chunkByDir=None,
                 **kwargs):
        platform = platform.split('-')[0]
        self.test_suites = test_suites
        self.totalChunks = totalChunks
        self.thisChunk = thisChunk
        self.chunkByDir = chunkByDir

        testEnv = MozillaEnvironments['%s-unittest' % platform].copy()
        testEnv['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(platform)
        testEnv['MINIDUMP_SAVE_PATH'] = WithProperties('%(basedir:-)s/minidumps')
        testEnv.update(env)

        self.leak_thresholds = {'mochitest-plain': mochitest_leak_threshold,
                                'crashtest': crashtest_leak_threshold,}

        MozillaTestFactory.__init__(self, platform, productName, env=testEnv,
                                    downloadTests=True,
                                    **kwargs)

    def addSetupSteps(self):
        if 'linux' in self.platform:
            self.addStep(ShellCommand(
                name='disable_screensaver',
                command=['xset', 's', 'off', 's', 'reset'],
                env=self.env,
            ))
        if self.platform.startswith('win32'):
            self.addStep(ShellCommand(
                name='run mouse & screen adjustment script',
                command=['C:\\mozilla-build\\python25\\python.exe',
          	  WithProperties('%(toolsdir)s/scripts/support/mouse_and_screen_resolution.py'),
          	  '--configuration-url',
          	  WithProperties("http://%s/%s" % (self.hgHost, self.repoPath) + \
                                 "/raw-file/%(revision)s/testing/machine-configuration.json")],
                flunkOnFailure=True,
                haltOnFailure=True,
                log_eval_func=lambda c,s: regex_log_evaluator(c, s, global_errors),
            ))

    def addRunTestSteps(self):
        if self.platform.startswith('macosx64'):
            self.addStep(resolution_step())
        # Run them!
        if self.downloadSymbolsOnDemand:
            symbols_path = '%(symbols_url)s'
        else:
            symbols_path = 'symbols'
        for suite in self.test_suites:
            leak_threshold = self.leak_thresholds.get(suite, None)
            if suite.startswith('mobile-mochitest'):
                # Mobile specific mochitests need a couple things to be
                # set differently compared to non-mobile specific tests
                real_suite = suite[len('mobile-'):]
                # Unpack the tests
                self.addStep(UnpackTest(
                 filename=WithProperties('%(tests_filename)s'),
                 testtype='mochitest',
                 haltOnFailure=True,
                 name='unpack mochitest tests',
                 ))

                variant = real_suite.split('-', 1)[1]
                self.addStep(unittest_steps.MozillaPackagedMochitests(
                 variant=variant,
                 env=self.env,
                 symbols_path=symbols_path,
                 testPath='mobile',
                 leakThreshold=leak_threshold,
                 chunkByDir=self.chunkByDir,
                 totalChunks=self.totalChunks,
                 thisChunk=self.thisChunk,
                 maxTime=90*60, # One and a half hours, to allow for slow hardware
                ))
            elif suite.startswith('mochitest'):
                # Unpack the tests
                self.addStep(UnpackTest(
                 filename=WithProperties('%(tests_filename)s'),
                 testtype='mochitest',
                 haltOnFailure=True,
                 name='unpack mochitest tests',
                 ))

                variant = suite.split('-', 1)[1]
                self.addStep(unittest_steps.MozillaPackagedMochitests(
                 variant=variant,
                 env=self.env,
                 symbols_path=symbols_path,
                 leakThreshold=leak_threshold,
                 chunkByDir=self.chunkByDir,
                 totalChunks=self.totalChunks,
                 thisChunk=self.thisChunk,
                 maxTime=120*60, # Two hours for slow debug tests
                ))
            elif suite == 'xpcshell':
                # Unpack the tests
                self.addStep(UnpackTest(
                 filename=WithProperties('%(tests_filename)s'),
                 testtype='xpcshell',
                 haltOnFailure=True,
                 name='unpack xpcshell tests',
                 ))

                self.addStep(unittest_steps.MozillaPackagedXPCShellTests(
                 env=self.env,
                 platform=self.platform,
                 symbols_path=symbols_path,
                 maxTime=120*60, # Two Hours
                ))
            elif suite in ('jsreftest', ):
                # Specialized runner for jsreftest because they take so long to unpack and clean up
                # Unpack the tests
                self.addStep(UnpackTest(
                 filename=WithProperties('%(tests_filename)s'),
                 testtype='jsreftest',
                 haltOnFailure=True,
                 name='unpack jsreftest tests',
                 ))

                self.addStep(unittest_steps.MozillaPackagedReftests(
                 suite=suite,
                 env=self.env,
                 leakThreshold=leak_threshold,
                 symbols_path=symbols_path,
                 maxTime=2*60*60, # Two Hours
                ))
            elif suite == 'jetpack':
                # Unpack the tests
                self.addStep(UnpackTest(
                 filename=WithProperties('%(tests_filename)s'),
                 testtype='jetpack',
                 haltOnFailure=True,
                 name='unpack jetpack tests',
                 ))

                self.addStep(unittest_steps.MozillaPackagedJetpackTests(
                  suite=suite,
                  env=self.env,
                  leakThreshold=leak_threshold,
                  symbols_path=symbols_path,
                  maxTime=120*60, # Two Hours
                 ))
            elif suite in ('reftest', 'reftest-ipc', 'reftest-d2d', 'crashtest', \
                           'crashtest-ipc', 'direct3D', 'opengl', 'opengl-no-accel', \
                           'reftest-no-d2d-d3d'):
                if suite in ('direct3D', 'opengl'):
                    self.env.update({'MOZ_ACCELERATED':'11'})
                if suite in ('reftest-ipc', 'crashtest-ipc'):
                    self.env.update({'MOZ_LAYERS_FORCE_SHMEM_SURFACES':'1'})
                # Unpack the tests
                self.addStep(UnpackTest(
                 filename=WithProperties('%(tests_filename)s'),
                 testtype='reftest',
                 haltOnFailure=True,
                 name='unpack reftest tests',
                 ))
                self.addStep(unittest_steps.MozillaPackagedReftests(
                 suite=suite,
                 env=self.env,
                 leakThreshold=leak_threshold,
                 symbols_path=symbols_path,
                 maxTime=2*60*60, # Two Hours
                ))
            elif suite == 'mozmill':
                MOZMILL_VIRTUALENV_DIR = os.path.join('..', 'mozmill-virtualenv')
                mozmill_env = self.env.copy()
                mozmill_env['MOZMILL_NO_VNC'] = "1"
                mozmill_env['NO_EM_RESTART'] = "0"
                mozmill_env['MOZMILL_RICH_FAILURES'] = "1"

                # Workaround for Mozrunner bug 575863.
                if 'LD_LIBRARY_PATH' in mozmill_env:
                    mozmill_env['LD_LIBRARY_PATH'] = WithProperties("../%(exedir)s")

                # Find the application app bundle, workaround for mozmill on OS X
                #XXX
                self.brandName = "Shredder"
                if self.platform.startswith('macosx'):
                    self.addStep(SetProperty(
                        name='Find executable .app',
                        command=['bash', '-c', WithProperties('echo %(exedir)s | cut -d/ -f1-2') ],
                        property='exepath',
                        workdir='.',
                    ))
                self.addStep(UnpackTest(
                 filename=WithProperties('%(tests_filename)s'),
                 testtype='mozmill',
                 haltOnFailure=True,
                 name='unpack mozmill tests',
                 ))
                self.addStep(ShellCommand(
                  name='install plugins',
                  command=['sh', '-c', WithProperties('if [ ! -d %(exedir)s/plugins ]; then mkdir %(exedir)s/plugins; fi && cp -R bin/plugins/* %(exedir)s/plugins/')],
                  haltOnFailure=True,
                  ))

                # Older comm-central branches use a centrally-installed
                # MozMill. We figure this out by seeing if installmozmill.py is
                # present.
                self.addStep(SetProperty(
                  name='check mozmill virtualenv setup',
                  property='mozmillVirtualenvSetup',
                  command=['bash', '-c', 'test -e installmozmill.py && ls installmozmill.py'],
                  workdir='build/mozmill/resources',
                  flunkOnFailure=False,
                  haltOnFailure=False,
                  warnOnFailure=False
                ))
                def isVirtualenvSetup(step):
                    return (step.build.getProperties().has_key("mozmillVirtualenvSetup") and
                            len(step.build.getProperty("mozmillVirtualenvSetup")) > 0)

                # We want to use system python on non-Windows
                virtualenv_python = 'python' if self.platform.startswith('win') else '/usr/bin/python'

                self.addStep(ShellCommand(
                  name='setup virtualenv',
                  command=[virtualenv_python, 'resources/installmozmill.py',
                           MOZMILL_VIRTUALENV_DIR],
                  doStepIf=isVirtualenvSetup,
                  flunkOnFailure=True,
                  haltOnFailure=True,
                  workdir='build/mozmill'
                  ))
                bindir = 'Scripts' if self.platform.startswith('win') else 'bin'

                # PYTHONHOME overrides virtualenv install directories, so get rid of it
                mozmill_virtualenv_env = mozmill_env.copy()
                mozmill_virtualenv_env['PYTHONHOME'] = None

                mozmillpython = os.path.join(MOZMILL_VIRTUALENV_DIR, bindir, 'python')
                self.addStep(unittest_steps.MozillaCheck,
                  test_name="mozmill virtualenv",
                  warnOnWarnings=True,
                  command = ['bash', '-c', WithProperties(mozmillpython + ' runtestlist.py --binary=../%(exepath)s --symbols-path=' + symbols_path + ' --list=mozmilltests.list')],
                  doStepIf=isVirtualenvSetup,
                  env=mozmill_virtualenv_env,
                  workdir='build/mozmill',
                )
                # ... and add another step for older branches, run if the above isn't
                self.addStep(unittest_steps.MozillaCheck,
                  test_name="mozmill legacy",
                  warnOnWarnings=True,
                  command=['python', 'runtestlist.py', WithProperties('--binary=../%(exepath)s') ,'--symbols-path=' + symbols_path,'--list=mozmilltests.list'],
                  doStepIf=lambda step: not isVirtualenvSetup(step),
                  env=mozmill_env,
                  workdir='build/mozmill',
                )

        if self.platform.startswith('macosx64'):
            self.addStep(resolution_step())


class RemoteUnittestFactory(MozillaTestFactory):
    def __init__(self, platform, suites, hostUtils, productName='fennec',
                downloadSymbols=False, downloadTests=True, posixBinarySuffix='',
                remoteExtras=None, branchName=None, **kwargs):
        self.suites = suites
        self.hostUtils = WithProperties(hostUtils)

        if remoteExtras is not None:
            self.remoteExtras = remoteExtras
        else:
            self.remoteExtras = {}

        exePaths = self.remoteExtras.get('processName', {})
        if branchName in exePaths:
            self.remoteProcessName = exePaths[branchName]
        else:
            if 'default' in exePaths:
                self.remoteProcessName = exePaths['default']
            else:
                self.remoteProcessName = 'org.mozilla.fennec'

        env = {}
        env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(platform)
        env['MINIDUMP_SAVE_PATH'] = WithProperties('%(basedir:-)s/minidumps')

        MozillaTestFactory.__init__(self, platform, productName=productName,
                                    downloadSymbols=downloadSymbols,
                                    downloadTests=downloadTests,
                                    posixBinarySuffix=posixBinarySuffix,
                                    env=env, **kwargs)

    def addCleanupSteps(self):
        '''Clean up the relevant places before starting a build'''
        #On windows, we should try using cmd's attrib and native rmdir
        self.addStep(ShellCommand(
            name='rm_builddir',
            command=['rm', '-rf', 'build'],
            workdir='.'
        ))

    def addInitialSteps(self):
        self.addStep(SetProperty(
             command=['bash', '-c', 'echo $SUT_IP'],
             property='sut_ip'
        ))
        MozillaTestFactory.addInitialSteps(self)
        self.addStep(ShellCommand(
         name="verify_tegra_state",
         description="Running verify.py",
         command=['python', '-u', '/builds/sut_tools/verify.py'],
         workdir='build',
         haltOnFailure=True,
         log_eval_func=rc_eval_func({0: SUCCESS, None: RETRY}),
        ))
        self.addStep(SetProperty(
            name="GetFoopyPlatform",
            command=['bash', '-c', 'uname -s'],
            property='foopy_type'
        ))

    def addSetupSteps(self):
        self.addStep(DownloadFile(
            url=self.hostUtils,
            filename_property='hostutils_filename',
            url_property='hostutils_url',
            haltOnFailure=True,
            ignore_certs=self.ignoreCerts,
            name='download_hostutils',
        ))
        self.addStep(UnpackFile(
            filename=WithProperties('../%(hostutils_filename)s'),
            scripts_dir='../tools/buildfarm/utils',
            haltOnFailure=True,
            workdir='build/hostutils',
            name='unpack_hostutils',
        ))
        self.addStep(ShellCommand(
            name='install app on device',
            workdir='.',
            description="Install App on Device",
            command=['python', '/builds/sut_tools/installApp.py',
                     WithProperties("%(sut_ip)s"),
                     WithProperties("build/%(build_filename)s"),
                     self.remoteProcessName,
                    ],
            haltOnFailure=True)
        )

    def addPrepareBuildSteps(self):
        def get_build_url(build):
            '''Make sure that there is at least one build in the file list'''
            assert len(build.source.changes[-1].files) > 0, 'Unittest sendchange has no files'
            return parse_sendchange_files(build, exclude_substrs=['.crashreporter-symbols.',
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
            filename=WithProperties('../%(build_filename)s'),
            scripts_dir='../tools/buildfarm/utils',
            haltOnFailure=True,
            workdir='build/%s' % self.productName,
            name='unpack_build',
        ))
        def get_robocop_url(build):
            '''We assume 'robocop.apk' is in same directory as the
            main apk, so construct url based on the build_url property
            set when we downloaded that.
            '''
            build_url = build.getProperty('build_url')
            build_url = build_url[:build_url.rfind('/')]
            robocop_url = build_url + '/robocop.apk'
            return robocop_url

        # the goal of bug 715215 is to download robocop.apk if we
        # think it will be needed. We can tell that by the platform
        # being 'android' and 'robocop' being mentioned in the suite
        # name. (The suite name must include 'robocop', as that data
        # driven feature is used to append the robocop options to a
        # command line.)
        if "android" in self.platform and 'robocop' in self.suites[0]['suite']:
            self.addStep(DownloadFile(
                url_fn=get_robocop_url,
                filename_property='robocop_filename',
                url_property='robocop_url',
                haltOnFailure=True,
                ignore_certs=self.ignoreCerts,
                name='download_robocop',
            ))
        self.addStep(SetBuildProperty(
         property_name="exedir",
         value=self.productName
        ))

    def addRunTestSteps(self):
        if self.downloadSymbolsOnDemand:
            symbols_path = '%(symbols_url)s'
        else:
            symbols_path = '../symbols'

        for suite in self.suites:
            name = suite['suite']

            self.addStep(ShellCommand(
                name='configure device',
                workdir='.',
                description="Configure Device",
                command=['python', '/builds/sut_tools/config.py',
                         WithProperties("%(sut_ip)s"),
                         name,
                        ],
                haltOnFailure=True)
            )
            if name.startswith('mochitest'):
                self.addStep(UnpackTest(
                 filename=WithProperties('../%(tests_filename)s'),
                 testtype='mochitest',
                 workdir='build/tests',
                 haltOnFailure=True,
                ))
                variant = name.split('-', 1)[1]
                if 'browser-chrome' in name:
                    stepProc = unittest_steps.RemoteMochitestBrowserChromeStep
                else:
                    stepProc = unittest_steps.RemoteMochitestStep
                if suite.get('testPaths', None):
                    for tp in suite.get('testPaths', []):
                        self.addStep(stepProc(
                         variant=variant,
                         symbols_path=symbols_path,
                         testPath=tp,
                         workdir='build/tests',
                         timeout=2400,
                         app=self.remoteProcessName,
                         env=self.env,
                         log_eval_func=lambda c,s: regex_log_evaluator(c, s,
                          global_errors + tegra_errors),
                        ))
                else:
                    totalChunks = suite.get('totalChunks', None)
                    thisChunk = suite.get('thisChunk', None)
                    self.addStep(stepProc(
                     variant=variant,
                     symbols_path=symbols_path,
                     testManifest=suite.get('testManifest', None),
                     workdir='build/tests',
                     timeout=2400,
                     app=self.remoteProcessName,
                     env=self.env,
                     totalChunks=totalChunks,
                     thisChunk=thisChunk,
                     log_eval_func=lambda c,s: regex_log_evaluator(c, s,
                      global_errors + tegra_errors),
                    ))
            elif name.startswith('reftest') or name == 'crashtest':
                totalChunks = suite.get('totalChunks', None)
                thisChunk = suite.get('thisChunk', None)
                # Unpack the tests
                self.addStep(UnpackTest(
                 filename=WithProperties('../%(tests_filename)s'),
                 testtype='reftest',
                 workdir='build/tests',
                 haltOnFailure=True,
                 ))
                self.addStep(unittest_steps.RemoteReftestStep(
                 suite=name,
                 symbols_path=symbols_path,
                 totalChunks=totalChunks,
                 thisChunk=thisChunk,
                 workdir='build/tests',
                 timeout=2400,
                 app=self.remoteProcessName,
                 env=self.env,
                 cmdOptions=self.remoteExtras.get('cmdOptions'),
                 log_eval_func=lambda c,s: regex_log_evaluator(c, s,
                     global_errors + tegra_errors),
                ))
            elif name == 'jsreftest':
                totalChunks = suite.get('totalChunks', None)
                thisChunk = suite.get('thisChunk', None)
                self.addStep(UnpackTest(
                 filename=WithProperties('../%(tests_filename)s'),
                 testtype='jsreftest',
                 workdir='build/tests',
                 haltOnFailure=True,
                 ))
                self.addStep(unittest_steps.RemoteReftestStep(
                 suite=name,
                 symbols_path=symbols_path,
                 totalChunks=totalChunks,
                 thisChunk=thisChunk,
                 workdir='build/tests',
                 timeout=2400,
                 app=self.remoteProcessName,
                 env=self.env,
                 cmdOptions=self.remoteExtras.get('cmdOptions'),
                 log_eval_func=lambda c,s: regex_log_evaluator(c, s,
                     global_errors + tegra_errors),
                ))

    def addTearDownSteps(self):
        self.addCleanupSteps()
        self.addStep(ShellCommand(
            name='reboot device',
            workdir='.',
            alwaysRun=True,
            warnOnFailure=False,
            flunkOnFailure=False,
            timeout=60*30,
            description='Reboot Device',
            command=['python', '-u', '/builds/sut_tools/reboot.py',
                      WithProperties("%(sut_ip)s"),
                     ],
            log_eval_func=lambda c,s: SUCCESS,
        ))

class TalosFactory(RequestSortingBuildFactory):
    extName = 'addon.xpi'
    """Create working talos build factory"""
    def __init__(self, OS, supportUrlBase, envName, buildBranch, branchName,
            configOptions, talosCmd, customManifest=None, customTalos=None,
            workdirBase=None, fetchSymbols=False, plugins=None, pagesets=[],
            remoteTests=False, productName="firefox", remoteExtras=None,
            talosAddOns=[], releaseTester=False, credentialsFile=None,
            talosBranch=None, branch=None, talos_from_source_code=False,
            datazillaUrl=None):

        BuildFactory.__init__(self)

        if workdirBase is None:
            workdirBase = "."

        self.workdirBase = workdirBase
        self.OS = OS
        self.supportUrlBase = supportUrlBase
        self.buildBranch = buildBranch
        self.branchName = branchName
        self.ignoreCerts = False
        if self.branchName.lower().startswith('shadow'):
            self.ignoreCerts = True
        self.remoteTests = remoteTests
        self.configOptions = configOptions['suites'][:]
        try:
            self.suites = self.configOptions[self.configOptions.index("--activeTests")+1]
        except:
            # simple-talos does not use --activeTests
            self.suites = ""
        self.talosCmd = talosCmd[:]
        self.customManifest = customManifest
        self.customTalos = customTalos
        self.fetchSymbols = fetchSymbols
        self.plugins = plugins
        self.pagesets = pagesets[:]
        self.talosAddOns = talosAddOns[:]
        self.exepath = None
        self.env = MozillaEnvironments[envName]
        self.releaseTester = releaseTester
        self.productName = productName
        self.remoteExtras = remoteExtras
        self.talos_from_source_code = talos_from_source_code
        self.credentialsFile = credentialsFile

        if datazillaUrl:
            self.talosCmd.extend(['--datazilla-url', datazillaUrl])
            self.talosCmd.extend(['--authfile', os.path.basename(credentialsFile)])
        if talosBranch is None:
            self.talosBranch = branchName
        else:
            self.talosBranch = talosBranch

        if self.remoteExtras is not None:
            exePaths = self.remoteExtras.get('processName', {})
        else:
            exePaths = {}
        if branch in exePaths:
            self.remoteProcessName = exePaths[branch]
        else:
            if 'default' in exePaths:
                self.remoteProcessName = exePaths['default']
            else:
                self.remoteProcessName = 'org.mozilla.fennec'

        self.addInfoSteps()
        if self.remoteTests:
            self.addMobileCleanupSteps()
        self.addCleanupSteps()
        self.addDmgInstaller()
        self.addDownloadBuildStep()
        self.addUnpackBuildSteps()
        self.addGetBuildInfoStep()
        if fetchSymbols:
            self.addDownloadSymbolsStep()
        self.addSetupSteps()
        self.addPluginInstallSteps()
        self.addPagesetInstallSteps()
        self.addAddOnInstallSteps()
        if self.remoteTests:
            self.addPrepareDeviceStep()
        self.addUpdateConfigStep()
        self.addRunTestStep()
        self.addCleanupSteps()
        self.addRebootStep()

    def pythonWithJson(self, platform):
        '''
        Return path to a python version that eithers has "simplejson" or
        it is 2.6 or higher (which includes the json module)
        '''
        if (platform in ("fedora", "fedora64", "leopard", "snowleopard", "lion", "mountainlion")):
            return "/tools/buildbot/bin/python"
        elif (platform in ('w764', 'win7', 'xp')):
            return "C:\\mozilla-build\\python25\\python.exe"
        elif (platform.find("android") > -1):
            # path in the foopies
            return "/usr/local/bin/python2.6"
        else:
            raise ValueError("No valid platform was passed: %s" % platform)

    def _propertyIsSet(self, step, prop):
        return step.build.getProperties().has_key(prop)

    def addInfoSteps(self):
        if self.remoteTests:
            self.addStep(SetProperty(
                 command=['bash', '-c', 'echo $SUT_IP'],
                 property='sut_ip'
            ))

    def addMobileCleanupSteps(self):
        self.addStep(ShellCommand(
         name="verify_tegra_state",
         description="Running verify.py",
         command=['python', '-u', '/builds/sut_tools/verify.py'],
         workdir='build',
         haltOnFailure=True,
         log_eval_func=rc_eval_func({0: SUCCESS, None: RETRY}),
        ))

    def addCleanupSteps(self):
        if self.OS in ('xp', 'vista', 'win7', 'w764'):
            #required step due to long filename length in tp4
            self.addStep(ShellCommand(
             name='mv tp4',
             workdir=os.path.join(self.workdirBase),
             flunkOnFailure=False,
             warnOnFailure=False,
             description="move tp4 out of talos dir to tp4-%random%",
             command=["if", "exist", "talos\\page_load_test\\tp4", "mv", "talos\\page_load_test\\tp4", "tp4-%random%"],
             env=self.env)
            )
            #required step due to long filename length in tp5
            self.addStep(ShellCommand(
             name='mv tp5',
             workdir=os.path.join(self.workdirBase),
             flunkOnFailure=False,
             warnOnFailure=False,
             description="move tp5 out of talos dir to tp5-%random%",
             command=["if", "exist", "talos\\page_load_test\\tp5", "mv", "talos\\page_load_test\\tp5", "tp5-%random%"],
             env=self.env)
            )
            self.addStep(ShellCommand(
             name='chmod_files',
             workdir=self.workdirBase,
             flunkOnFailure=False,
             warnOnFailure=False,
             description="chmod files (see msys bug)",
             command=["chmod", "-R", "a+rwx", "."],
             env=self.env)
            )
            #on windows move the whole working dir out of the way, saves us trouble later
            self.addStep(ShellCommand(
             name='move old working dir out of the way',
             workdir=os.path.dirname(self.workdirBase),
             description="move working dir",
             command=["if", "exist", os.path.basename(self.workdirBase), "mv", os.path.basename(self.workdirBase), "t-%random%"],
             env=self.env)
            )
            self.addStep(ShellCommand(
             name='remove any old working dirs',
             workdir=os.path.dirname(self.workdirBase),
             description="remove old working dirs",
             command='if exist t-* nohup rm -rf t-*',
             env=self.env)
            )
            self.addStep(ShellCommand(
             name='create new working dir',
             workdir=os.path.dirname(self.workdirBase),
             description="create new working dir",
             command='mkdir ' + os.path.basename(self.workdirBase),
             env=self.env)
            )
        else:
            self.addStep(ShellCommand(
             name='cleanup',
             workdir=self.workdirBase,
             description="Cleanup",
             command='nohup rm -rf *',
             env=self.env)
            )
        if 'fed' in self.OS:
            self.addStep(ShellCommand(
                name='disable_screensaver',
                command=['xset', 's', 'off', 's', 'reset']))
        self.addStep(ShellCommand(
         name='create talos dir',
         workdir=self.workdirBase,
         description="talos dir creation",
         command='mkdir talos',
         env=self.env)
        )
        if not self.remoteTests:
            self.addStep(DownloadFile(
             url=WithProperties("%s/tools/buildfarm/maintenance/count_and_reboot.py" % self.supportUrlBase),
             workdir=self.workdirBase,
             haltOnFailure=True,
            ))

    def addDmgInstaller(self):
        if self.OS in ('leopard', 'tiger', 'snowleopard', 'lion', 'mountainlion'):
            self.addStep(DownloadFile(
             url=WithProperties("%s/tools/buildfarm/utils/installdmg.sh" % self.supportUrlBase),
             workdir=self.workdirBase,
             haltOnFailure=True,
            ))

    def addDownloadBuildStep(self):
        def get_url(build):
            url = build.source.changes[-1].files[0]
            url = urllib.unquote(url)
            return url
        self.addStep(DownloadFile(
         url_fn=get_url,
         url_property="fileURL",
         filename_property="filename",
         workdir=self.workdirBase,
         haltOnFailure=True,
         ignore_certs=self.ignoreCerts,
         name="Download build",
        ))
        if '--fennecIDs' in self.configOptions:
            def get_fennec_ids_url(build):
                url = build.source.changes[-1].files[0]
                return url.rsplit("/", 1)[0] + "/fennec_ids.txt"

            def get_robocop_url(build):
                url = build.source.changes[-1].files[0]
                return url.rsplit("/", 1)[0] + "/robocop.apk"

            self.addStep(DownloadFile(
             url_fn=get_fennec_ids_url,
             url_property="fennec_ids_url",
             filename_property="fennec_ids_filename",
             workdir=self.workdirBase,
             haltOnFailure=True,
             ignore_certs=self.ignoreCerts,
             name="download_fennec_ids",
             description="Download fennec_ids.txt",
            ))

            self.addStep(DownloadFile(
             url_fn=get_robocop_url,
             url_property='robocop_url',
             filename_property='robocop_filename',
             workdir=self.workdirBase + "/build",
             haltOnFailure=True,
             ignore_certs=self.ignoreCerts,
             name='download_robocop',
             description="Download robocop.apk",
            ))

    def addUnpackBuildSteps(self):
        if (self.releaseTester and (self.OS in ('xp', 'vista', 'win7', 'w764'))):
            #build is packaged in a windows installer
            self.addStep(DownloadFile(
             url=WithProperties("%s/tools/buildfarm/utils/firefoxInstallConfig.ini" % self.supportUrlBase),
             haltOnFailure=True,
             workdir=self.workdirBase,
            ))
            self.addStep(SetProperty(
              name='set workdir path',
              command=['pwd'],
              property='workdir_pwd',
              workdir=self.workdirBase,
            ))
            self.addStep(ShellCommand(
             name='install_release_build',
             workdir=self.workdirBase,
             description="install windows release build",
             command=[WithProperties('%(filename)s'), WithProperties('/INI=%(workdir_pwd)s\\firefoxInstallConfig.ini')],
             env=self.env)
            )
        elif self.OS.startswith('tegra_android') or self.OS.startswith('panda_android'):
            self.addStep(UnpackFile(
             filename=WithProperties("../%(filename)s"),
             workdir="%s/%s" % (self.workdirBase, self.productName),
             name="Unpack build",
             haltOnFailure=True,
            ))
        else:
            self.addStep(UnpackFile(
             filename=WithProperties("%(filename)s"),
             workdir=self.workdirBase,
             name="Unpack build",
             haltOnFailure=True,
            ))
        if self.OS in ('xp', 'vista', 'win7', 'w764'):
            self.addStep(ShellCommand(
             name='chmod_files',
             workdir=os.path.join(self.workdirBase, "%s/" % self.productName),
             flunkOnFailure=False,
             warnOnFailure=False,
             description="chmod files (see msys bug)",
             command=["chmod", "-R", "a+x", "."],
             env=self.env)
            )
        if self.OS in ('tiger', 'leopard', 'snowleopard', 'lion', 'mountainlion'):
            self.addStep(FindFile(
             workdir=os.path.join(self.workdirBase, "talos"),
             filename="%s-bin" % self.productName,
             directory="..",
             max_depth=4,
             property_name="exepath",
             name="Find executable",
            ))
        elif self.OS in ('xp', 'vista', 'win7', 'w764'):
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="../%s/%s" % (self.productName, self.productName)
            ))
        elif self.OS.startswith('tegra_android') or self.OS.startswith('panda_android'):
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="../%s/%s" % (self.productName, self.productName)
            ))
        else:
            if self.productName == 'fennec':
                exeName = self.productName
            else:
                exeName = "%s-bin" % self.productName
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="../%s/%s" % (self.productName, exeName)
            ))
        self.exepath = WithProperties('%(exepath)s')

    def addGetBuildInfoStep(self):
        def get_exedir(build):
            return os.path.dirname(build.getProperty('exepath'))
        self.addStep(SetBuildProperty(
         property_name="exedir",
         value=get_exedir,
        ))

        # Figure out which revision we're running
        def get_build_info(rc, stdout, stderr):
            retval = {'repo_path': None,
                      'revision': None,
                      'buildid': None,
                     }
            stdout = "\n".join([stdout, stderr])
            m = re.search("^BuildID\s*=\s*(\w+)", stdout, re.M)
            if m:
                retval['buildid'] = m.group(1)
            m = re.search("^SourceStamp\s*=\s*(.*)", stdout, re.M)
            if m:
                retval['revision'] = m.group(1).strip()
            m = re.search("^SourceRepository\s*=\s*(\S+)", stdout, re.M)
            if m:
                retval['repo_path'] = m.group(1)
            return retval

        self.addStep(SetProperty(
         command=['cat', WithProperties('%(exedir)s/application.ini')],
         workdir=os.path.join(self.workdirBase, "talos"),
         extract_fn=get_build_info,
         name='get build info',
        ))

        def check_sdk(cmd, step):
            txt = cmd.logs['stdio'].getText()
            m = re.search("MacOSX10\.5\.sdk", txt, re.M)
            if m :
                step.addCompleteLog('sdk-fail', 'TinderboxPrint: Skipping tests; can\'t run 10.5 based build on 10.4 slave')
                return FAILURE
            return SUCCESS
        if self.OS == "tiger":
            self.addStep(ShellCommand(
                command=['bash', '-c',
                         WithProperties('unzip -c %(exedir)s/chrome/toolkit.jar content/global/buildconfig.html | grep sdk')],
                workdir=os.path.join(self.workdirBase, "talos"),
                log_eval_fn=check_sdk,
                haltOnFailure=True,
                flunkOnFailure=False,
                name='check sdk okay'))

    def addSetupSteps(self):
        if self.credentialsFile:
            target_file_name = os.path.basename(self.credentialsFile)
            self.addStep(FileDownload(
                mastersrc=self.credentialsFile,
                slavedest=target_file_name,
                workdir=os.path.join(self.workdirBase, "talos"),
                flunkOnFailure=False,
            ))
        if self.customManifest:
            self.addStep(FileDownload(
             mastersrc=self.customManifest,
             slavedest="tp3.manifest",
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
             haltOnFailure=True,
             ))

        if self.customTalos is None and not self.remoteTests:
            if self.talos_from_source_code:
                self.addStep(DownloadFile(
                    url=WithProperties("%(repo_path)s/raw-file/%(revision)s/testing/talos/talos_from_code.py"),
                    workdir=self.workdirBase,
                    haltOnFailure=True,
                    wget_args=['--progress=dot:mega', '--no-check-certificate'],
                    log_eval_func=lambda c,s: regex_log_evaluator(c, s, talos_hgweb_errors),
                ))
                self.addStep(ShellCommand(
                    name='download files specified in talos.json',
                    command=[self.pythonWithJson(self.OS), 'talos_from_code.py', \
                            '--talos-json-url', \
                            WithProperties('%(repo_path)s/raw-file/%(revision)s/testing/talos/talos.json')],
                    workdir=self.workdirBase,
                    haltOnFailure=True,
                    log_eval_func=lambda c,s: regex_log_evaluator(c, s, talos_hgweb_errors),
                ))
            else:
                self.addStep(DownloadFile(
                  url=WithProperties("%s/zips/talos.zip" % self.supportUrlBase),
                  workdir=self.workdirBase,
                  haltOnFailure=True,
                ))
                self.addStep(DownloadFile(
                 url=WithProperties("%s/xpis/pageloader.xpi" % self.supportUrlBase),
                 workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
                 haltOnFailure=True,
                 ))

            self.addStep(UnpackFile(
             filename='talos.zip',
             workdir=self.workdirBase,
             haltOnFailure=True,
            ))
        elif self.remoteTests:
            self.addStep(DownloadFile(
             url='http://build.mozilla.org/talos/zips/retry.zip',
             haltOnFailure=True,
             ignore_certs=self.ignoreCerts,
             name='download_retry_zip',
             workdir=self.workdirBase,
            ))
            self.addStep(UnpackFile(
             filename='retry.zip',
             haltOnFailure=True,
             name='unpack_retry_zip',
             workdir=self.workdirBase,
            ))
            self.addStep(SetProperty(
             name='set_toolsdir',
             command=['bash', '-c', 'echo `pwd`'],
             property='toolsdir',
             workdir=self.workdirBase,
            ))
            if self.talos_from_source_code:
                self.addStep(RetryingShellCommand(
                 name='get_talos_from_code_py',
                 description="Downloading talos_from_code.py",
                 command=['wget', '--no-check-certificate',
                          WithProperties("%(repo_path)s/raw-file/%(revision)s/testing/talos/talos_from_code.py")],
                 workdir=self.workdirBase,
                 haltOnFailure=True,
                 log_eval_func=lambda c,s: regex_log_evaluator(c, s, talos_hgweb_errors),
                ))
                self.addStep(RetryingShellCommand(
                 name='run_talos_from_code_py',
                 description="Running talos_from_code.py",
                 command=[self.pythonWithJson(self.OS), 'talos_from_code.py', \
                         '--talos-json-url', \
                         WithProperties('%(repo_path)s/raw-file/%(revision)s/testing/talos/talos.json')],
                 workdir=self.workdirBase,
                 haltOnFailure=True,
                 log_eval_func=lambda c,s: regex_log_evaluator(c, s, talos_hgweb_errors),
                ))
            else:
                self.addStep(RetryingShellCommand(
                 name='get_talos_zip',
                 command=['wget', '-O', 'talos.zip', '--no-check-certificate',
                          'http://build.mozilla.org/talos/zips/talos.mobile.old.zip'],
                 workdir=self.workdirBase,
                 haltOnFailure=True,
                ))
            self.addStep(UnpackFile(
             filename='talos.zip',
             workdir=self.workdirBase,
             haltOnFailure=True,
             description="Unpack talos.zip",
            ))
            if self.suites.find('tp4m') != -1:
                self.addStep(DownloadFile(
                 url='http://build.mozilla.org/talos/zips/mobile_tp4.zip',
                 workdir=self.workdirBase + "/talos",
                 haltOnFailure=True,
                 description="Download mobile_tp4.zip",
                ))
                self.addStep(UnpackFile(
                 filename='mobile_tp4.zip',
                 workdir=self.workdirBase + "/talos",
                 haltOnFailure=True,
                 description="Unpack mobile_tp4.zip",
                ))
        else:
            self.addStep(FileDownload(
             mastersrc=self.customTalos,
             slavedest=self.customTalos,
             workdir=self.workdirBase,
             blocksize=640*1024,
             haltOnFailure=True,
            ))
            self.addStep(UnpackFile(
             filename=self.customTalos,
             workdir=self.workdirBase,
             haltOnFailure=True,
            ))

    def addPluginInstallSteps(self):
        if self.plugins:
            #32 bit (includes mac browsers)
            if self.OS in ('xp', 'vista', 'win7', 'fedora', 'tegra_android',
                           'tegra_android-armv6', 'tegra_android-noion', 'panda_android',
                           'leopard', 'snowleopard', 'leopard-o', 'lion',
                           'mountainlion'):
                self.addStep(DownloadFile(
                 url=WithProperties("%s/%s" % (self.supportUrlBase, self.plugins['32'])),
                 workdir=os.path.join(self.workdirBase, "talos/base_profile"),
                 haltOnFailure=True,
                ))
                self.addStep(UnpackFile(
                 filename=os.path.basename(self.plugins['32']),
                 workdir=os.path.join(self.workdirBase, "talos/base_profile"),
                 haltOnFailure=True,
                ))
            #64 bit
            if self.OS in ('w764', 'fedora64'):
                self.addStep(DownloadFile(
                 url=WithProperties("%s/%s" % (self.supportUrlBase, self.plugins['64'])),
                 workdir=os.path.join(self.workdirBase, "talos/base_profile"),
                 haltOnFailure=True,
                ))
                self.addStep(UnpackFile(
                 filename=os.path.basename(self.plugins['64']),
                 workdir=os.path.join(self.workdirBase, "talos/base_profile"),
                 haltOnFailure=True,
                ))

    def addPagesetInstallSteps(self):
        for pageset in self.pagesets:
            self.addStep(DownloadFile(
             url=WithProperties("%s/%s" % (self.supportUrlBase, pageset)),
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
             haltOnFailure=True,
            ))
            self.addStep(UnpackFile(
             filename=os.path.basename(pageset),
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
             haltOnFailure=True,
            ))

    def addAddOnInstallSteps(self):
        for addOn in self.talosAddOns:
            self.addStep(DownloadFile(
             url=WithProperties("%s/%s" % (self.supportUrlBase, addOn)),
             workdir=os.path.join(self.workdirBase, "talos"),
             haltOnFailure=True,
            ))
            self.addStep(UnpackFile(
             filename=os.path.basename(addOn),
             workdir=os.path.join(self.workdirBase, "talos"),
             haltOnFailure=True,
            ))

    def addDownloadSymbolsStep(self):
        def get_symbols_url(build):
            suffixes = ('.tar.bz2', '.dmg', '.zip', '.apk')
            buildURL = build.getProperty('fileURL')

            for suffix in suffixes:
                if buildURL.endswith(suffix):
                    return buildURL[:-len(suffix)] + '.crashreporter-symbols.zip'

        self.addStep(DownloadFile(
         url_fn=get_symbols_url,
         filename_property="symbolsFile",
         workdir=self.workdirBase,
         ignore_certs=self.ignoreCerts,
         name="Download symbols",
        ))
        self.addStep(ShellCommand(
         name="mkdir_symbols",
         command=['mkdir', 'symbols'],
         workdir=self.workdirBase,
        ))
        self.addStep(UnpackFile(
         filename=WithProperties("../%(symbolsFile)s"),
         workdir="%s/symbols" % self.workdirBase,
         name="Unpack symbols",
        ))

    def addPrepareDeviceStep(self):
        self.addStep(ShellCommand(
            name='install app on device',
            workdir=self.workdirBase,
            description="Install App on Device",
            command=['python', '/builds/sut_tools/installApp.py',
                     WithProperties("%(sut_ip)s"),
                     WithProperties(self.workdirBase + "/%(filename)s"),
                     self.remoteProcessName,
                    ],
            env=self.env,
            haltOnFailure=True)
        )

    def addUpdateConfigStep(self):
        self.addStep(talos_steps.MozillaUpdateConfig(
         workdir=os.path.join(self.workdirBase, "talos/"),
         branch=self.buildBranch,
         branchName=self.talosBranch,
         remoteTests=self.remoteTests,
         haltOnFailure=True,
         executablePath=self.exepath,
         addOptions=self.configOptions,
         env=self.env,
         extName=TalosFactory.extName,
         useSymbols=self.fetchSymbols,
         remoteExtras=self.remoteExtras,
         remoteProcessName=self.remoteProcessName)
        )

    def addRunTestStep(self):
        if self.OS in ('mountainlion', 'lion', 'snowleopard'):
            self.addStep(resolution_step())
        self.addStep(talos_steps.MozillaRunPerfTests(
         warnOnWarnings=True,
         workdir=os.path.join(self.workdirBase, "talos/"),
         timeout=3600,
         maxTime=10800,
         haltOnFailure=False,
         command=self.talosCmd,
         env=self.env)
        )
        if self.OS in ('mountainlion', 'lion', 'snowleopard'):
            self.addStep(resolution_step())

    def addRebootStep(self):
        if self.OS in ('mountainlion', 'lion',):
            self.addStep(ShellCommand(
                name="clear_saved_state",
                flunkOnFailure=False,
                warnOnFailure=False,
                haltOnFailure=False,
                workdir='/Users/cltbld',
                command=['bash', '-c', 'rm -rf Library/Saved\ Application\ State/*.savedState']
            ))
        def do_disconnect(cmd):
            try:
                if 'SCHEDULED REBOOT' in cmd.logs['stdio'].getText():
                    return True
            except:
                pass
            return False
        if self.remoteTests:
            self.addStep(ShellCommand(
                         name='reboot device',
                         flunkOnFailure=False,
                         warnOnFailure=False,
                         alwaysRun=True,
                         workdir=self.workdirBase,
                         description="Reboot Device",
                         timeout=60*30,
                         command=['python', '-u', '/builds/sut_tools/reboot.py',
                                  WithProperties("%(sut_ip)s"),
                                 ],
                         env=self.env,
                         log_eval_func=lambda c,s: SUCCESS,
            ))
        else:
            #the following step is to help the linux running on mac minis reboot cleanly
            #see bug561442
            if 'fedora' in self.OS:
                self.addStep(ShellCommand(
                    name='set_time',
                    description=['set', 'time'],
                    alwaysRun=True,
                    command=['bash', '-c',
                             'sudo hwclock --set --date="$(date +%m/%d/%y\ %H:%M:%S)"'],
                ))

            self.addStep(DisconnectStep(
             name='reboot',
             flunkOnFailure=False,
             warnOnFailure=False,
             alwaysRun=True,
             workdir=self.workdirBase,
             description="reboot after 1 test run",
             command=["python", "count_and_reboot.py", "-f", "../talos_count.txt", "-n", "1", "-z"],
             force_disconnect=do_disconnect,
             env=self.env,
            ))

class RuntimeTalosFactory(TalosFactory):
    def __init__(self, configOptions=None, plugins=None, pagesets=None,
                 supportUrlBase=None, talosAddOns=None,
                 *args, **kwargs):
        if not configOptions:
            # TalosFactory/MozillaUpdateConfig require this format for this variable
            # MozillaUpdateConfig allows for adding additional options at runtime,
            # which is how this factory is intended to be used.
            configOptions = {'suites': []}
        # For the rest of these, make them overridable with WithProperties
        if not plugins:
            plugins = {'32': '%(plugin)s', '64': '%(plugin)s'}
        if not pagesets:
            pagesets = ['%(pageset1)s', '%(pageset2)s']
        if not supportUrlBase:
            supportUrlBase = '%(supportUrlBase)s'
        if not talosAddOns:
            talosAddOns = ['%(talosAddon1)s', '%(talosAddon2)s']
        TalosFactory.__init__(self, *args, configOptions=configOptions,
                              plugins=plugins, pagesets=pagesets,
                              supportUrlBase=supportUrlBase,
                              talosAddOns=talosAddOns,
                              **kwargs)

    def addInfoSteps(self):
        pass

    def addPluginInstallSteps(self):
        if self.plugins:
            self.addStep(DownloadFile(
                url=WithProperties("%s/%s" % (self.supportUrlBase, '%(plugin)s')),
                workdir=os.path.join(self.workdirBase, "talos/base_profile"),
                doStepIf=lambda step: self._propertyIsSet(step, 'plugin'),
                filename_property='plugin_base'
            ))
            self.addStep(UnpackFile(
                filename=WithProperties('%(plugin_base)s'),
                workdir=os.path.join(self.workdirBase, "talos/base_profile"),
                doStepIf=lambda step: self._propertyIsSet(step, 'plugin')
            ))

    def addPagesetInstallSteps(self):
        # XXX: This is really hacky, it would be better to extract the property
        # name from the format string.
        n = 1
        for pageset in self.pagesets:
            self.addStep(DownloadFile(
             url=WithProperties("%s/%s" % (self.supportUrlBase, pageset)),
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
             doStepIf=lambda step, n=n: self._propertyIsSet(step, 'pageset%d' % n),
             filename_property='pageset%d_base' % n
            ))
            self.addStep(UnpackFile(
             filename=WithProperties('%(pageset' + str(n) + '_base)s'),
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
             doStepIf=lambda step, n=n: self._propertyIsSet(step, 'pageset%d' % n)
            ))
            n += 1

    def addAddOnInstallSteps(self):
        n = 1
        for addOn in self.talosAddOns:
            self.addStep(DownloadFile(
             url=WithProperties("%s/%s" % (self.supportUrlBase, addOn)),
             workdir=os.path.join(self.workdirBase, "talos"),
             doStepIf=lambda step, n=n: self._propertyIsSet(step, 'talosAddon%d' % n),
             filename_property='talosAddon%d_base' % n
            ))
            self.addStep(UnpackFile(
             filename=WithProperties('%(talosAddon' + str(n) + '_base)s'),
             workdir=os.path.join(self.workdirBase, "talos"),
             doStepIf=lambda step, n=n: self._propertyIsSet(step, 'talosAddon%d' % n)
            ))
            n += 1


class PartnerRepackFactory(ReleaseFactory):
    def getReleaseTag(self, product, version):
        return product.upper() + '_' + \
               str(version).replace('.','_') + '_' + \
               'RELEASE'

    def __init__(self, productName, version, partnersRepoPath,
                 stagingServer, stageUsername, stageSshKey,
                 buildNumber=1, partnersRepoRevision='default',
                 nightlyDir="nightly", platformList=None, packageDmg=True,
                 partnerUploadDir='unsigned/partner-repacks',
                 baseWorkDir='.', python='python', **kwargs):
        ReleaseFactory.__init__(self, baseWorkDir=baseWorkDir, **kwargs)
        self.productName = productName
        self.version = version
        self.buildNumber = buildNumber
        self.partnersRepoPath = partnersRepoPath
        self.partnersRepoRevision = partnersRepoRevision
        self.stagingServer = stagingServer
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey
        self.partnersRepackDir = '%s/partner-repacks' % self.baseWorkDir
        self.partnerUploadDir = partnerUploadDir
        self.packageDmg = packageDmg
        self.python = python
        self.platformList = platformList
        self.candidatesDir = self.getCandidatesDir(productName,
                                                   version,
                                                   buildNumber,
                                                   nightlyDir=nightlyDir)
        self.releaseTag = self.getReleaseTag(productName, version)
        self.extraRepackArgs = []
        if nightlyDir:
            self.extraRepackArgs.extend(['--nightly-dir', '%s/%s' % \
                                        (productName, nightlyDir)])
        if self.packageDmg:
            self.extraRepackArgs.extend(['--pkg-dmg',
                                        WithProperties('%(scriptsdir)s/pkg-dmg')])
        if platformList:
            for platform in platformList:
                self.extraRepackArgs.extend(['--platform', platform])

        self.getPartnerRepackData()
        self.doPartnerRepacks()
        self.uploadPartnerRepacks()

    def getPartnerRepackData(self):
        # We start fresh every time.
        self.addStep(ShellCommand(
            name='rm_partners_repo',
            command=['rm', '-rf', self.partnersRepackDir],
            description=['remove', 'partners', 'repo'],
            workdir=self.baseWorkDir,
        ))
        self.addStep(self.makeHgtoolStep(
            name='clone_partner_repacks',
            repo_url='http://%s/%s' % (self.hgHost, self.partnersRepoPath),
            wc=self.partnersRepackDir,
            workdir=self.baseWorkDir,
            rev=self.partnersRepoRevision,
            env=self.env,
            use_properties=False,
        ))
        if self.packageDmg:
            self.addStep(ShellCommand(
                name='download_pkg-dmg',
                command=['bash', '-c',
                         'wget http://hg.mozilla.org/%s/raw-file/%s/build/package/mac_osx/pkg-dmg' % (self.repoPath, self.releaseTag)],
                description=['download', 'pkg-dmg'],
                workdir='%s/scripts' % self.partnersRepackDir,
                haltOnFailure=True
            ))
            self.addStep(ShellCommand(
                name='chmod_pkg-dmg',
                command=['chmod', '755', 'pkg-dmg'],
                description=['chmod', 'pkg-dmg'],
                workdir='%s/scripts' % self.partnersRepackDir,
                haltOnFailure=True
            ))
            self.addStep(SetProperty(
                name='set_scriptsdir',
                command=['bash', '-c', 'pwd'],
                property='scriptsdir',
                workdir='%s/scripts' % self.partnersRepackDir,
            ))

    def doPartnerRepacks(self):
        if self.enableSigning and self.signingServers:
            self.extraRepackArgs.append('--signed')
            self.addGetTokenSteps()
        self.addStep(RepackPartners(
            name='repack_partner_builds',
            command=[self.python, './partner-repacks.py',
                     '--version', str(self.version),
                     '--build-number', str(self.buildNumber),
                     '--repo', self.repoPath,
                     '--hgroot', 'http://%s' % self.hgHost,
                     '--staging-server', self.stagingServer,
                     '--dmg-extract-script',
                     WithProperties('%(toolsdir)s/release/common/unpack-diskimage.sh'),
                    ] + self.extraRepackArgs,
            env=self.env,
            description=['repacking', 'partner', 'builds'],
            descriptionDone=['repacked', 'partner', 'builds'],
            workdir='%s/scripts' % self.partnersRepackDir,
            haltOnFailure=True
        ))

    def uploadPartnerRepacks(self):
        self.addStep(ShellCommand(
         name='upload_partner_builds',
         command=['rsync', '-av',
                  '-e', 'ssh -oIdentityFile=~/.ssh/%s' % self.stageSshKey,
                  'build%s/' % str(self.buildNumber),
                  '%s@%s:%s/' % (self.stageUsername,
                                self.stagingServer,
                                self.candidatesDir)
                  ],
         workdir='%s/scripts/repacked_builds/%s' % (self.partnersRepackDir,
                                                    self.version),
         description=['upload', 'partner', 'builds'],
         haltOnFailure=True
        ))

        if not self.enableSigning or not self.signingServers:
            for platform in self.platformList:
                self.addStep(ShellCommand(
                 name='create_partner_build_directory',
                 description=['create', 'partner', 'directory'],
                 command=['bash', '-c',
                    'ssh -oIdentityFile=~/.ssh/%s %s@%s mkdir -p %s/%s/'
                        % (self.stageSshKey, self.stageUsername,
                           self.stagingServer, self.candidatesDir,
                           self.partnerUploadDir),
                     ],
                 workdir='.',
                ))
                self.addStep(ShellCommand(
                 name='upload_partner_build_status',
                 command=['bash', '-c',
                    'ssh -oIdentityFile=~/.ssh/%s %s@%s touch %s/%s/%s'
                        % (self.stageSshKey, self.stageUsername,
                           self.stagingServer, self.candidatesDir,
                           self.partnerUploadDir, 'partner_build_%s' % platform),
                     ],
                 workdir='%s/scripts/repacked_builds/%s/build%s' % (self.partnersRepackDir,
                                                                    self.version,
                                                                    str(self.buildNumber)),
                 description=['upload', 'partner', 'status'],
                 haltOnFailure=True
                ))
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


class ScriptFactory(BuildFactory):

    def __init__(self, scriptRepo, scriptName, cwd=None, interpreter=None,
            extra_data=None, extra_args=None,
            script_timeout=1200, script_maxtime=None, log_eval_func=None,
            reboot_command=None, hg_bin='hg', platform=None,
            use_mock=False, mock_target=None,
            mock_packages=None, mock_copyin_files=None, env={}):
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
        self.env = env.copy()
        if platform and 'win' in platform:
            self.get_basedir_cmd = ['cmd', '/C', 'pwd']
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

        self.addStep(SetBuildProperty(
            property_name='master',
            value=lambda b: b.builder.botmaster.parent.buildbotURL
        ))
        self.addStep(SetProperty(
            name='get_basedir',
            property='basedir',
            command=self.get_basedir_cmd,
            workdir='.',
            haltOnFailure=True,
        ))
        self.env['PROPERTIES_FILE']= WithProperties('%(basedir)s/buildprops.json')
        self.addStep(JSONPropertiesDownload(
            name="download_props",
            slavedest="buildprops.json",
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
        self.addStep(ShellCommand(
            name="clobber_properties",
            command=['rm', '-rf', 'properties'],
            workdir=".",
        ))
        self.addStep(ShellCommand(
            name="clobber_scripts",
            command=['rm', '-rf', 'scripts'],
            workdir=".",
            haltOnFailure=True,
        ))
        self.addStep(MercurialCloneCommand(
            name="clone_scripts",
            command=[hg_bin, 'clone', scriptRepo, 'scripts'],
            workdir=".",
            haltOnFailure=True,
            retry=False,
        ))
        self.addStep(ShellCommand(
            name="update_scripts",
            command=[hg_bin, 'update', '-C', '-r',
                     WithProperties('%(script_repo_revision:-default)s')],
            haltOnFailure=True,
            workdir='scripts'
        ))
        self.runScript()
        self.reboot()

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
                        name='mock_copyin_%s' % source.replace('/','_'),
                        command=['mock_mozilla', '-r', self.mock_target,
                                 '--copyin', source, target],
                        haltOnFailure=True,
                    ))
                    self.addStep(MockCommand(
                        name='mock_chown_%s' % target.replace('/','_'),
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
            ))

    def runScript(self):
        self.preRunScript()
        self.addStep(MockCommand(
            name="run_script",
            command=self.cmd,
            env=self.env,
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
            # note: prefixing 'type' command with '@' to suppress extraneous output
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
            self.addStep(SetProperty(
                name='set_toolsdir',
                command=self.get_basedir_cmd,
                property='toolsdir',
                workdir='scripts',
            ))
            self.addStep(SetProperty(
                name='set_basedir',
                command=self.get_basedir_cmd,
                property='basedir',
                workdir='.',
            ))
            self.env['MOZ_SIGN_CMD'] = WithProperties(get_signing_cmd(
                self.signingServers, self.env.get('PYTHON26')))
            self.env['MOZ_SIGNING_SERVERS'] = ",".join(s[0] for s in self.signingServers)

        ScriptFactory.runScript(self)
