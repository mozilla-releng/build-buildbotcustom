from datetime import datetime
import os.path, re
from time import strftime
import urllib

from twisted.python import log

from buildbot.process.factory import BuildFactory
from buildbot.steps import trigger
from buildbot.steps.shell import ShellCommand, WithProperties, SetProperty
from buildbot.steps.source import Mercurial
from buildbot.steps.transfer import FileDownload

import buildbotcustom.steps.misc
import buildbotcustom.steps.release
import buildbotcustom.steps.test
import buildbotcustom.steps.transfer
import buildbotcustom.steps.updates
import buildbotcustom.steps.talos
import buildbotcustom.steps.unittest
import buildbotcustom.env
reload(buildbotcustom.steps.misc)
reload(buildbotcustom.steps.release)
reload(buildbotcustom.steps.test)
reload(buildbotcustom.steps.transfer)
reload(buildbotcustom.steps.updates)
reload(buildbotcustom.steps.talos)
reload(buildbotcustom.steps.unittest)
reload(buildbotcustom.env)

from buildbotcustom.steps.misc import TinderboxShellCommand, SendChangeStep, \
  GetBuildID, MozillaClobberer, FindFile, DownloadFile, UnpackFile, \
  SetBuildProperty, GetHgRevision, DisconnectStep, OutputStep, \
  EvaluatingShellCommand, RepackPartners
from buildbotcustom.steps.release import UpdateVerify, L10nVerifyMetaDiff
from buildbotcustom.steps.test import AliveTest, CompareBloatLogs, \
  CompareLeakLogs, Codesighs, GraphServerPost
from buildbotcustom.steps.transfer import MozillaStageUpload
from buildbotcustom.steps.updates import CreateCompleteUpdateSnippet
from buildbotcustom.env import MozillaEnvironments

import buildbotcustom.steps.unittest as unittest_steps

import buildbotcustom.steps.talos as talos_steps
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, SKIPPED, EXCEPTION

class BootstrapFactory(BuildFactory):
    def __init__(self, automation_tag, logdir, bootstrap_config,
                 cvsroot="pserver:anonymous@cvs-mirror.mozilla.org",
                 cvsmodule="mozilla"):
        """
    @type  cvsroot: string
    @param cvsroot: The CVSROOT to use for checking out Bootstrap.

    @type  cvsmodule: string
    @param cvsmodule: The CVS module to use for checking out Bootstrap.

    @type  automation_tag: string
    @param automation_tag: The CVS Tag to use for checking out Bootstrap.

    @type  logdir: string
    @param logdir: The log directory for Bootstrap to use.
                   Note - will be created if it does not already exist.

    @type  bootstrap_config: string
    @param bootstrap_config: The location of the bootstrap.cfg file on the
                             slave. This will be copied to "bootstrap.cfg"
                             in the builddir on the slave.
        """
        BuildFactory.__init__(self)
        self.addStep(ShellCommand,
         name='rm_builddir',
         description='clean checkout',
         workdir='.',
         command=['rm', '-rf', 'build'],
         haltOnFailure=1)
        self.addStep(ShellCommand,
         name='checkout',
         description='checkout',
         workdir='.',
         command=['cvs', '-d', cvsroot, 'co', '-r', automation_tag,
                  '-d', 'build', cvsmodule],
         haltOnFailure=1,
        )
        self.addStep(ShellCommand,
         name='copy_bootstrap',
         description='copy bootstrap.cfg',
         command=['cp', bootstrap_config, 'bootstrap.cfg'],
         haltOnFailure=1,
        )
        self.addStep(ShellCommand,
         name='echo_bootstrap',
         description='echo bootstrap.cfg',
         command=['cat', 'bootstrap.cfg'],
         haltOnFailure=1,
        )
        self.addStep(ShellCommand,
         name='create_logdir',
         description='(re)create logs area',
         command=['bash', '-c', 'mkdir -p ' + logdir],
         haltOnFailure=1,
        )

        self.addStep(ShellCommand,
         name='rm_old_logs',
         description='clean logs area',
         command=['bash', '-c', 'rm -rf ' + logdir + '/*.log'],
         haltOnFailure=1,
        )
        self.addStep(ShellCommand,
         name='make_test',
         description='unit tests',
         command=['make', 'test'],
         haltOnFailure=1,
        )

def getPlatformMinidumpPath(platform):
    platform_minidump_path = {
        'linux': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
        'win32': WithProperties('%(toolsdir:-)s/breakpad/win32/minidump_stackwalk.exe'),
        'macosx': WithProperties('%(toolsdir:-)s/breakpad/osx/minidump_stackwalk'),
        }
    return platform_minidump_path[platform]

class MozillaBuildFactory(BuildFactory):
    ignore_dirs = [
            'info',
            'repo_setup',
            'tag',
            'source',
            'updates',
            'final_verification',
            'l10n_verification',
            'macosx_update_verify',
            'macosx_build',
            'macosx_repack',
            'win32_update_verify',
            'win32_build',
            'win32_repack',
            'linux_update_verify',
            'linux_build',
            'linux_repack'
            ]

    def __init__(self, hgHost, repoPath, buildToolsRepoPath, buildSpace=0,
                 clobberURL=None, clobberTime=None, buildsBeforeReboot=None,
                 branchName=None, triggerBuilds=False,
                 triggeredSchedulers=None, hashType='sha512', **kwargs):
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
        self.triggerBuilds = triggerBuilds
        self.triggeredSchedulers = triggeredSchedulers
        self.hashType = hashType

        self.repository = self.getRepository(repoPath)
        if branchName:
          self.branchName = branchName
        else:
          self.branchName = self.getRepoName(self.repository)

        self.addStep(OutputStep(
         name='get_buildername',
         data=WithProperties('Building on: %(slavename)s'),
        ))
        self.addStep(OutputStep(
         name='tinderboxprint_buildername',
         data=WithProperties('TinderboxPrint: s: %(slavename)s'),
        ))
        self.addStep(OutputStep(
         name='tinderboxsummarymessage_buildername',
         data=WithProperties('TinderboxSummaryMessage: s: %(slavename)s'),
        ))
        self.addInitialSteps()

    def addInitialSteps(self):
        self.addStep(ShellCommand,
         name='rm_buildtools',
         command=['rm', '-rf', 'tools'],
         description=['clobber', 'build tools'],
         workdir='.'
        )
        self.addStep(ShellCommand,
         name='clone_buildtools',
         command=['bash', '-c',
          'if [ ! -d tools ]; then hg clone %s tools;fi' % self.buildToolsRepo],
         description=['clone', 'build tools'],
         workdir='.'
        )
        self.addStep(SetProperty,
         command=['bash', '-c', 'pwd'],
         property='toolsdir',
         workdir='tools'
        )
        # We need the basename of the current working dir so we can
        # ignore that dir when purging builds later.
        self.addStep(SetProperty,
         name='basename_pwd',
         command=['bash', '-c', 'basename "$PWD"'],
         property='builddir',
         workdir='.'
        )

        if self.clobberURL is not None:
            self.addStep(MozillaClobberer,
             name='checking_clobber_times',
             branch=self.branchName,
             clobber_url=self.clobberURL,
             clobberer_path=WithProperties('%(builddir)s/tools/clobberer/clobberer.py'),
             clobberTime=self.clobberTime
            )

        if self.buildSpace > 0:
            command = ['python', 'tools/buildfarm/maintenance/purge_builds.py',
                 '-s', str(self.buildSpace)]

            for i in self.ignore_dirs:
                command.extend(["-n", i])
            # Ignore the current dir also.
            command.extend(["-n", WithProperties("%(builddir)s")])
            # These are the base_dirs that get passed to purge_builds.py.
            # The scratchbox dir is only present on linux slaves, but since
            # not all classes that inherit from MozillaBuildFactory provide
            # a platform property we can use for limiting the base_dirs, it
            # is easier to include scratchbox by default and simply have
            # purge_builds.py skip the dir when it isn't present.
            command.extend(["..","/scratchbox/users/cltbld/home/cltbld/build"])

            self.addStep(ShellCommand,
             name='clean_old_builds',
             command=command,
             description=['cleaning', 'old', 'builds'],
             descriptionDone=['clean', 'old', 'builds'],
             haltOnFailure=True,
             workdir='.',
             timeout=3600, # One hour, because Windows is slow
            )

    def addTriggeredBuildsSteps(self,
                                triggeredSchedulers=None):
        '''Trigger other schedulers.
        We don't include these steps by default because different
        children may want to trigger builds at different stages.
        '''
        if triggeredSchedulers is None:
            if self.triggeredSchedulers is None:
                return True
            triggeredSchedulers = self.triggeredSchedulers

        for triggeredScheduler in triggeredSchedulers:
            self.addStep(trigger.Trigger(
                schedulerNames=[triggeredScheduler],
                waitForFinish=False))

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
        if repoPath.startswith('/'):
            repoPath = repoPath.lstrip('/')
        if not hgHost:
            hgHost = self.hgHost
        proto = 'ssh' if push else 'http'
        return '%s://%s/%s' % (proto, hgHost, repoPath)

    def getPackageFilename(self, platform):
        if platform.startswith("linux64"):
            packageFilename = '*.linux-x86_64.tar.bz2'
        elif platform.startswith("linux"):
            packageFilename = '*.linux-i686.tar.bz2'
        elif platform.startswith("macosx"):
            packageFilename = '*.dmg'
        elif platform.startswith("win32"):
            packageFilename = '*.win32.zip'
        elif platform.startswith("wince"):
            packageFilename = '*.wince-arm.zip'
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
                               maxDepth=1, haltOnFailure=False):
        self.addStep(FindFile(
            name='find_filepath',
            filename=filename,
            directory=directory,
            filetype='file',
            max_depth=maxDepth,
            property_name='filepath',
            workdir='.',
            haltOnFailure=haltOnFailure
        ))
        self.addStep(SetProperty,
            command=['basename', WithProperties('%(filepath)s')],
            property=fileType+'Filename',
            workdir='.',
            name='set_'+fileType.lower()+'_filename',
            haltOnFailure=haltOnFailure
        )
        self.addStep(SetProperty,
            command=['bash', '-c', 
                     WithProperties("ls -l %(filepath)s")],
            workdir='.',
            name='set_'+fileType.lower()+'_size',
            extract_fn = self.parseFileSize(propertyName=fileType+'Size'),
            haltOnFailure=haltOnFailure
        )
        self.addStep(SetProperty,
            command=['bash', '-c', 
                     WithProperties('openssl ' + 'dgst -' + self.hashType +
                                    ' %(filepath)s')],
            workdir='.',
            name='set_'+fileType.lower()+'_hash',
            extract_fn=self.parseFileHash(propertyName=fileType+'Hash'),
            haltOnFailure=haltOnFailure
        )   
        self.addStep(SetProperty,
            name='unset_filepath',
            command='echo "filepath:"',
            workdir=directory,
            extract_fn = self.unsetFilepath,
        )


class MercurialBuildFactory(MozillaBuildFactory):
    def __init__(self, env, objdir, platform, configRepoPath, configSubDir,
                 profiledBuild, mozconfig, productName=None, buildRevision=None,
                 stageServer=None, stageUsername=None, stageGroup=None,
                 stageSshKey=None, stageBasePath=None, ausBaseUploadDir=None,
                 updatePlatform=None, downloadBaseURL=None, ausUser=None,
                 ausHost=None, nightly=False, leakTest=False, checkTest=False,
                 valgrindCheck=False, codesighs=True, graphServer=None,
                 graphSelector=None, graphBranch=None, baseName=None,
                 uploadPackages=True, uploadSymbols=True, createSnippet=False,
                 doCleanup=True, packageSDK=False, packageTests=False,
                 mozillaDir=None, **kwargs):
        MozillaBuildFactory.__init__(self, **kwargs)
        self.env = env.copy()
        self.objdir = objdir
        self.platform = platform
        self.configRepoPath = configRepoPath
        self.configSubDir = configSubDir
        self.profiledBuild = profiledBuild
        self.mozconfig = mozconfig
        self.productName = productName
        self.buildRevision = buildRevision
        self.stageServer = stageServer
        self.stageUsername = stageUsername
        self.stageGroup = stageGroup
        self.stageSshKey = stageSshKey
        self.stageBasePath = stageBasePath
        self.ausBaseUploadDir = ausBaseUploadDir
        self.updatePlatform = updatePlatform
        self.downloadBaseURL = downloadBaseURL
        self.ausUser = ausUser
        self.ausHost = ausHost
        self.nightly = nightly
        self.leakTest = leakTest
        self.checkTest = checkTest
        self.valgrindCheck = valgrindCheck
        self.codesighs = codesighs
        self.graphServer = graphServer
        self.graphSelector = graphSelector
        self.graphBranch = graphBranch
        self.baseName = baseName
        self.uploadPackages = uploadPackages
        self.uploadSymbols = uploadSymbols
        self.createSnippet = createSnippet
        self.doCleanup = doCleanup
        self.packageSDK = packageSDK
        self.packageTests = packageTests

        if self.uploadPackages:
            assert productName and stageServer and stageUsername
            assert stageBasePath
        if self.createSnippet:
            assert ausBaseUploadDir and updatePlatform and downloadBaseURL
            assert ausUser and ausHost

            # this is a tad ugly because we need to python interpolation
            # as well as WithProperties
            # here's an example of what it translates to:
            # /opt/aus2/build/0/Firefox/mozilla2/WINNT_x86-msvc/2008010103/en-US
            self.ausFullUploadDir = '%s/%s/%%(buildid)s/en-US' % \
              (self.ausBaseUploadDir, self.updatePlatform)

        # we don't need the extra cruft in 'platform' anymore
        self.platform = platform.split('-')[0]
        assert self.platform in ('linux', 'linux64', 'win32', 'wince', 'macosx')

        if self.graphServer is not None:
            self.tbPrint = False
        else:
            self.tbPrint = True


        # Mozilla subdir and objdir
        if mozillaDir:
            self.mozillaDir = '/%s' % mozillaDir
            self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)
        else:
            self.mozillaDir = ''
            self.mozillaObjdir = self.objdir

        self.logUploadDir = 'tinderbox-builds/%s-%s/' % (self.branchName,
                                                         self.platform)
        self.addBuildSteps()
        if self.uploadSymbols or self.uploadPackages or self.leakTest:
            self.addBuildSymbolsStep()
        if self.uploadSymbols:
            self.addUploadSymbolsStep()
        if self.uploadPackages:
            self.addUploadSteps()
        if self.leakTest:
            self.addLeakTestSteps()
        if self.checkTest:
            self.addCheckTestSteps()
        if self.valgrindCheck:
            self.addValgrindCheckSteps()
        if self.codesighs:
            self.addCodesighsSteps()
        if self.createSnippet:
            self.addUpdateSteps()
        if self.triggerBuilds:
            self.addTriggeredBuildsSteps()
        if self.doCleanup:
            self.addPostBuildCleanupSteps()
        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def addBuildSteps(self):
        self.addPreBuildSteps()
        self.addSourceSteps()
        self.addConfigSteps()
        self.addDoBuildSteps()

    def addPreBuildSteps(self):
        if self.nightly:
            self.addStep(ShellCommand,
             name='rm_builddir',
             command=['rm', '-rf', 'build'],
             env=self.env,
             workdir='.',
             timeout=60*60 # 1 hour
            )
        self.addStep(ShellCommand,
         name='rm_old_pkg',
         command="rm -rf %s/dist/%s-* %s/dist/install/sea/*.exe " %
                  (self.mozillaObjdir, self.productName, self.mozillaObjdir),
         env=self.env,
         description=['deleting', 'old', 'package'],
         descriptionDone=['delete', 'old', 'package']
        )
        if self.nightly:
            self.addStep(ShellCommand,
             name='rm_old_symbols',
             command="find 20* -maxdepth 2 -mtime +7 -exec rm -rf {} \;",
             env=self.env,
             workdir='.',
             description=['cleanup', 'old', 'symbols'],
             flunkOnFailure=False
            )

    def addSourceSteps(self):
        self.addStep(Mercurial,
         name='hg_update',
         mode='update',
         baseURL='http://%s/' % self.hgHost,
         defaultBranch=self.repoPath,
         timeout=60*60, # 1 hour
        )
        if self.buildRevision:
            self.addStep(ShellCommand,
             name='hg_update',
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             haltOnFailure=True
            )
            self.addStep(SetProperty,
             name='set_got_revision',
             command=['hg', 'identify', '-i'],
             property='got_revision'
            )
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost,
                                                      self.repoPath)
        changesetLink += '/%(got_revision)s title="Built from revision %(got_revision)s">rev:%(got_revision)s</a>'
        self.addStep(OutputStep(
         name='tinderboxprint_changeset',
         data=['TinderboxPrint:', WithProperties(changesetLink)]
        ))

    def addConfigSteps(self):
        assert self.configRepoPath is not None
        assert self.configSubDir is not None
        assert self.mozconfig is not None
        configRepo = self.getRepository(self.configRepoPath)

        self.mozconfig = 'configs/%s/%s/mozconfig' % (self.configSubDir,
                                                      self.mozconfig)
        self.addStep(ShellCommand,
         name='rm_configs',
         command=['rm', '-rf', 'configs'],
         description=['removing', 'configs'],
         descriptionDone=['remove', 'configs'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='hg_clone_configs',
         command=['hg', 'clone', configRepo, 'configs'],
         description=['checking', 'out', 'configs'],
         descriptionDone=['checkout', 'configs'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         # cp configs/mozilla2/$platform/$repo/$type/mozconfig .mozconfig
         name='cp_mozconfig',
         command=['cp', self.mozconfig, '.mozconfig'],
         description=['copying', 'mozconfig'],
         descriptionDone=['copy', 'mozconfig'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='cat_mozconfig',
         command=['cat', '.mozconfig'],
        )

    def addDoBuildSteps(self):
        buildcmd = 'build'
        if self.profiledBuild:
            buildcmd = 'profiledbuild'
        self.addStep(ShellCommand,
         name='compile',
         command=['make', '-f', 'client.mk', buildcmd],
         description=['compile'],
         env=self.env,
         haltOnFailure=True,
         timeout=5400 # 90 minutes, because windows PGO builds take a long time
        )

    def addLeakTestSteps(self):
        # we want the same thing run a few times here, with different
        # extraArgs
        leakEnv = self.env.copy()
        leakEnv['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        for args in [['-register'], ['-CreateProfile', 'default'],
                     ['-P', 'default']]:
            self.addStep(AliveTest,
                env=leakEnv,
                workdir='build/%s/_leaktest' % self.mozillaObjdir,
                extraArgs=args,
                warnOnFailure=True,
                haltOnFailure=True
            )
        # we only want this variable for this test - this sucks
        bloatEnv = leakEnv.copy()
        bloatEnv['XPCOM_MEM_BLOAT_LOG'] = '1'
        self.addStep(AliveTest,
         env=bloatEnv,
         workdir='build/%s/_leaktest' % self.mozillaObjdir,
         logfile='bloat.log',
         warnOnFailure=True,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='get_bloat_log',
         env=self.env,
         workdir='.',
         command=['wget', '-O', 'bloat.log.old',
                  'http://%s/pub/mozilla.org/%s/%s/bloat.log' % \
                    (self.stageServer, self.productName, self.logUploadDir)],
         warnOnFailure=True,
         flunkOnFailure=False
        )
        self.addStep(ShellCommand,
         name='mv_bloat_log',
         env=self.env,
         command=['mv', '%s/_leaktest/bloat.log' % self.mozillaObjdir,
                  '../bloat.log'],
        )
        self.addStep(ShellCommand,
         name='upload_bloat_log',
         env=self.env,
         command=['scp', '-o', 'User=%s' % self.stageUsername,
                  '-o', 'IdentityFile=~/.ssh/%s' % self.stageSshKey,
                  '../bloat.log',
                  '%s:%s/%s' % (self.stageServer, self.stageBasePath,
                                self.logUploadDir)]
        )
        self.addStep(CompareBloatLogs,
         name='compare_bloat_log',
         bloatLog='bloat.log',
         env=self.env,
         workdir='.',
         mozillaDir=self.mozillaDir,
         tbPrint=self.tbPrint,
         warnOnFailure=True,
         haltOnFailure=False
        )
        self.addStep(SetProperty,
          command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
          'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
          'App', 'BuildID'],
          property='buildid',
          workdir='.',
          description=['getting', 'buildid'],
          descriptionDone=['got', 'buildid']
        )
        self.addStep(SetProperty,
          command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
          'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
          'App', 'SourceStamp'],
          property='sourcestamp',
          workdir='.',
          description=['getting', 'sourcestamp'],
          descriptionDone=['got', 'sourcestamp']
        )
        if self.graphServer:
            self.addStep(GraphServerPost,
             server=self.graphServer,
             selector=self.graphSelector,
             branch=self.graphBranch,
             resultsname=self.baseName
            )
        self.addStep(AliveTest,
         env=leakEnv,
         workdir='build/%s/_leaktest' % self.mozillaObjdir,
         extraArgs=['--trace-malloc', 'malloc.log',
                    '--shutdown-leaks=sdleak.log'],
         timeout=3600, # 1 hour, because this takes a long time on win32
         warnOnFailure=True,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='get_malloc_log',
         env=self.env,
         workdir='.',
         command=['wget', '-O', 'malloc.log.old',
                  'http://%s/pub/mozilla.org/%s/%s/malloc.log' % \
                    (self.stageServer, self.productName, self.logUploadDir)]
        )
        self.addStep(ShellCommand,
         name='get_sdleak_log',
         env=self.env,
         workdir='.',
         command=['wget', '-O', 'sdleak.tree.old',
                  'http://%s/pub/mozilla.org/%s/%s/sdleak.tree' % \
                    (self.stageServer, self.productName, self.logUploadDir)]
        )
        self.addStep(ShellCommand,
         name='mv_malloc_log',
         env=self.env,
         command=['mv',
                  '%s/_leaktest/malloc.log' % self.mozillaObjdir,
                  '../malloc.log'],
        )
        self.addStep(ShellCommand,
         name='mv_sdleak_log',
         env=self.env,
         command=['mv',
                  '%s/_leaktest/sdleak.log' % self.mozillaObjdir,
                  '../sdleak.log'],
        )
        self.addStep(CompareLeakLogs,
         name='compare_current_leak_log',
         mallocLog='../malloc.log',
         platform=self.platform,
         env=self.env,
         objdir=self.mozillaObjdir,
         testname='current',
         tbPrint=self.tbPrint,
         warnOnFailure=True,
         haltOnFailure=True
        )
        if self.graphServer:
            self.addStep(GraphServerPost,
             server=self.graphServer,
             selector=self.graphSelector,
             branch=self.graphBranch,
             resultsname=self.baseName
            )
        self.addStep(CompareLeakLogs,
         name='compare_previous_leak_log',
         mallocLog='../malloc.log.old',
         platform=self.platform,
         env=self.env,
         objdir=self.mozillaObjdir,
         testname='previous'
        )
        self.addStep(ShellCommand,
         name='create_sdleak_tree',
         env=self.env,
         workdir='.',
         command=['bash', '-c',
                  'perl build%s/tools/trace-malloc/diffbloatdump.pl '
                  '--depth=15 --use-address /dev/null sdleak.log '
                  '> sdleak.tree' % self.mozillaDir],
         warnOnFailure=True,
         haltOnFailure=True
        )
        if self.platform in ('macosx', 'linux'):
            self.addStep(ShellCommand,
             name='create_sdleak_raw',
             env=self.env,
             workdir='.',
             command=['mv', 'sdleak.tree', 'sdleak.tree.raw']
            )
            self.addStep(ShellCommand,
             name='get_fix_stack',
             env=self.env,
             workdir='.',
             command=['/bin/bash', '-c',
                      'perl '
                      'build%s/tools/rb/fix-%s-stack.pl '
                      'sdleak.tree.raw '
                      '> sdleak.tree' % (self.mozillaDir, self.platform)],
             warnOnFailure=True,
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         name='upload_logs',
         env=self.env,
         command=['scp', '-o', 'User=%s' % self.stageUsername,
                  '-o', 'IdentityFile=~/.ssh/%s' % self.stageSshKey,
                  '../malloc.log', '../sdleak.tree',
                  '%s:%s/%s' % (self.stageServer, self.stageBasePath,
                                self.logUploadDir)]
        )
        self.addStep(ShellCommand,
         name='compare_sdleak_tree',
         env=self.env,
         workdir='.',
         command=['perl', 'build%s/tools/trace-malloc/diffbloatdump.pl' % self.mozillaDir,
                  '--depth=15', 'sdleak.tree.old', 'sdleak.tree']
        )

    def addCheckTestSteps(self):
        env = self.env.copy()
        env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        self.addStep(unittest_steps.MozillaCheck,
         test_name="check",
         warnOnWarnings=True,
         workdir="build/%s" % self.objdir,
         timeout=5*60, # 5 minutes.
         env=env,
        )

    def addValgrindCheckSteps(self):
        env = self.env.copy()
        env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        self.addStep(unittest_steps.MozillaCheck,
         test_name="check-valgrind",
         warnOnWarnings=True,
         workdir="build/%s/js/src" % self.mozillaObjdir,
         timeout=5*60, # 5 minutes.
         env=env,
        )

    def addUploadSteps(self, pkgArgs=None):
        pkgArgs = pkgArgs or []
        if self.packageSDK:
            self.addStep(ShellCommand,
             name='make_sdk',
             command=['make', '-f', 'client.mk', 'sdk'],
             env=self.env,
             workdir='build/',
             haltOnFailure=True
            )
        if self.packageTests:
            self.addStep(ShellCommand,
             name='make_pkg_tests',
             command=['make', 'package-tests'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True,
            )
        self.addStep(ShellCommand,
            name='make_pkg',
            command=['make', 'package'] + pkgArgs,
            env=self.env,
            workdir='build/%s' % self.objdir,
            haltOnFailure=True
        )
        # Get package details
        packageFilename = self.getPackageFilename(self.platform)
        if packageFilename:
            self.addFilePropertiesSteps(filename=packageFilename, 
                                        directory='build/%s/dist' % self.mozillaObjdir,
                                        fileType='package',
                                        haltOnFailure=True)
        # Windows special cases
        if self.platform.startswith("win32") and \
           self.productName != 'xulrunner':
            self.addStep(ShellCommand,
                name='make_installer',
                command=['make', 'installer'] + pkgArgs,
                env=self.env,
                workdir='build/%s' % self.objdir,
                haltOnFailure=True
            )
            self.addFilePropertiesSteps(filename='*.installer.exe', 
                                        directory='build/%s/dist/install/sea' % self.mozillaObjdir,
                                        fileType='installer',
                                        haltOnFailure=True)
        elif self.platform.startswith("wince"):
            self.addStep(ShellCommand,
                name='make_cab',
                command=['make', 'package', 'MOZ_PKG_FORMAT=CAB'] + pkgArgs,
                env=self.env,
                workdir='build/%s' % self.objdir,
                haltOnFailure=True
            )
            self.addFilePropertiesSteps(filename='*.wince-arm.cab', 
                                        directory='build/%s' % self.objdir,
                                        fileType='installer',
                                        maxDepth=3,
                                        haltOnFailure=True)
        if self.createSnippet:
            self.addStep(ShellCommand,
                name='make_update_pkg',
                command=['make', '-C',
                         '%s/tools/update-packaging' % self.mozillaObjdir],
                env=self.env,
                haltOnFailure=True
            )
            self.addFilePropertiesSteps(filename='*.complete.mar', 
                                        directory='build/%s/dist/update' % self.mozillaObjdir,
                                        fileType='completeMar',
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

        # Call out to a subclass to do the actual uploading
        self.doUpload()

    def addCodesighsSteps(self):
        self.addStep(ShellCommand,
         name='make_codesighs',
         command=['make'],
         workdir='build/%s/tools/codesighs' % self.mozillaObjdir
        )
        self.addStep(ShellCommand,
         name='get_codesize_log',
         command=['wget', '-O', 'codesize-auto-old.log',
          'http://%s/pub/mozilla.org/%s/%s/codesize-auto.log' % \
           (self.stageServer, self.productName, self.logUploadDir)],
         workdir='.',
         env=self.env
        )
        if self.mozillaDir == '':
            codesighsObjdir = self.objdir
        else:
            codesighsObjdir = '../%s' % self.mozillaObjdir

        self.addStep(Codesighs,
         name='get_codesighs_diff',
         objdir=codesighsObjdir,
         platform=self.platform,
         workdir='build%s' % self.mozillaDir,
         env=self.env,
         tbPrint=self.tbPrint,
        )
        self.addStep(SetProperty,
          command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
          'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
          'App', 'BuildID'],
          property='buildid',
          workdir='.',
          description=['getting', 'buildid'],
          descriptionDone=['got', 'buildid']
        )
        self.addStep(SetProperty,
          command=['python', 'build%s/config/printconfigsetting.py' % self.mozillaDir,
          'build/%s/dist/bin/application.ini' % self.mozillaObjdir,
          'App', 'SourceStamp'],
          property='sourcestamp',
          workdir='.',
          description=['getting', 'sourcestamp'],
          descriptionDone=['got', 'sourcestamp']
        )
        if self.graphServer:
            self.addStep(GraphServerPost,
             server=self.graphServer,
             selector=self.graphSelector,
             branch=self.graphBranch,
             resultsname=self.baseName
            )
        self.addStep(ShellCommand,
         name='echo_codesize_log',
         command=['cat', '../codesize-auto-diff.log'],
         workdir='build%s' % self.mozillaDir
        )
        self.addStep(ShellCommand,
         name='upload_codesize_log',
         command=['scp', '-o', 'User=%s' % self.stageUsername,
          '-o', 'IdentityFile=~/.ssh/%s' % self.stageSshKey,
          '../codesize-auto.log',
          '%s:%s/%s' % (self.stageServer, self.stageBasePath,
                        self.logUploadDir)],
         workdir='build%s' % self.mozillaDir
        )

    def addUpdateSteps(self):
        self.addStep(CreateCompleteUpdateSnippet,
         objdir='build/%s' % self.mozillaObjdir,
         milestone=self.branchName,
         baseurl='%s/nightly' % self.downloadBaseURL,
         hashType=self.hashType
        )
        self.addStep(ShellCommand,
         name='create_aus_updir',
         command=['ssh', '-l', self.ausUser, self.ausHost,
                  WithProperties('mkdir -p %s' % self.ausFullUploadDir)],
         description=['create', 'aus', 'upload', 'dir'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='upload_complete_snippet',
         command=['scp', '-o', 'User=%s' % self.ausUser,
                  'dist/update/complete.update.snippet',
                  WithProperties('%s:%s/complete.txt' % \
                    (self.ausHost, self.ausFullUploadDir))],
         workdir='build/%s' % self.mozillaObjdir,
         description=['upload', 'complete', 'snippet'],
         haltOnFailure=True
        )

    def addBuildSymbolsStep(self):
        self.addStep(ShellCommand,
         name='make_buildsymbols',
         command=['make', 'buildsymbols'],
         env=self.env,
         workdir='build/%s' % self.objdir,
         haltOnFailure=True,
         timeout=60*60,
        )

    def addUploadSymbolsStep(self):
        self.addStep(ShellCommand,
         name='make_uploadsymbols',
         command=['make', 'uploadsymbols'],
         env=self.env,
         workdir='build/%s' % self.objdir,
         haltOnFailure=True
        )

    def addPostBuildCleanupSteps(self):
        if self.nightly:
            self.addStep(ShellCommand,
             name='rm_builddir',
             command=['rm', '-rf', 'build'],
             env=self.env,
             workdir='.',
             timeout=5400 # 1.5 hours
            )

class CCMercurialBuildFactory(MercurialBuildFactory):
    def __init__(self, skipBlankRepos=False, mozRepoPath='',
                 inspectorRepoPath='', venkmanRepoPath='',
                 chatzillaRepoPath='', cvsroot='', **kwargs):
        self.skipBlankRepos = skipBlankRepos
        self.mozRepoPath = mozRepoPath
        self.inspectorRepoPath = inspectorRepoPath
        self.venkmanRepoPath = venkmanRepoPath
        self.chatzillaRepoPath = chatzillaRepoPath
        self.cvsroot = cvsroot
        MercurialBuildFactory.__init__(self, mozillaDir='mozilla', **kwargs)

    def addSourceSteps(self):
        self.addStep(Mercurial, 
         name='hg_update',
         mode='update',
         baseURL='http://%s/' % self.hgHost,
         defaultBranch=self.repoPath,
         alwaysUseLatest=True,
         timeout=60*60 # 1 hour
        )

        if self.buildRevision:
            self.addStep(ShellCommand,
             name='hg_update',
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             haltOnFailure=True
            )
            self.addStep(SetProperty,
             name='hg_ident_revision',
             command=['hg', 'identify', '-i'],
             property='got_revision'
            )
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost, self.repoPath)
        changesetLink += '/%(got_revision)s title="Built from revision %(got_revision)s">rev:%(got_revision)s</a>'
        self.addStep(OutputStep(
         name='tinderboxprint_changeset',
         data=['TinderboxPrint:', WithProperties(changesetLink)]
        ))
        # build up the checkout command with all options
        co_command = ['python', 'client.py', 'checkout']
        if self.mozRepoPath:
            co_command.append('--mozilla-repo=%s' % self.getRepository(self.mozRepoPath))
        if self.inspectorRepoPath:
            co_command.append('--inspector-repo=%s' % self.getRepository(self.inspectorRepoPath))
        elif self.skipBlankRepos:
            co_command.append('--skip-inspector')
        if self.venkmanRepoPath:
            co_command.append('--venkman-repo=%s' % self.getRepository(self.venkmanRepoPath))
        elif self.skipBlankRepos:
            co_command.append('--skip-venkman')
        if self.chatzillaRepoPath:
            co_command.append('--chatzilla-repo=%s' % self.getRepository(self.chatzillaRepoPath))
        elif self.skipBlankRepos:
            co_command.append('--skip-chatzilla')
        if self.cvsroot:
            co_command.append('--cvsroot=%s' % self.cvsroot)
        if self.buildRevision:
            co_command.append('--comm-rev=%s' % self.buildRevision)
            co_command.append('--mozilla-rev=%s' % self.buildRevision)
            co_command.append('--inspector-rev=%s' % self.buildRevision)
            co_command.append('--venkman-rev=%s' % self.buildRevision)
            co_command.append('--chatzilla-rev=%s' % self.buildRevision)
        # execute the checkout
        self.addStep(ShellCommand,
         command=co_command,
         description=['running', 'client.py', 'checkout'],
         descriptionDone=['client.py', 'checkout'],
         haltOnFailure=True,
         timeout=60*60 # 1 hour
        )

        self.addStep(SetProperty,
         name='set_hg_revision',
         command=['hg', 'identify', '-i'],
         workdir='build%s' % self.mozillaDir,
         property='hg_revision'
        )
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost, self.mozRepoPath)
        changesetLink += '/%(hg_revision)s title="Built from Mozilla revision %(hg_revision)s">moz:%(hg_revision)s</a>'
        self.addStep(OutputStep(
         name='tinderboxprint_changeset',
         data=['TinderboxPrint:', WithProperties(changesetLink)]
        ))

    def addUploadSteps(self, pkgArgs=None):
        MercurialBuildFactory.addUploadSteps(self, pkgArgs)
        self.addStep(ShellCommand,
         command=['make', 'package-compare'],
         workdir='build/%s' % self.objdir,
         haltOnFailure=False
        )


class NightlyBuildFactory(MercurialBuildFactory):
    def __init__(self, talosMasters=None, unittestMasters=None,
            unittestBranch=None, tinderboxBuildsDir=None, 
            geriatricMasters=None, geriatricBranches=None, **kwargs):

        self.talosMasters = talosMasters or []

        self.unittestMasters = unittestMasters or []
        self.unittestBranch = unittestBranch

        if self.unittestMasters:
            assert self.unittestBranch

        self.tinderboxBuildsDir = tinderboxBuildsDir

        self.geriatricMasters = geriatricMasters or []
        #geriatricBranches is a mapping between MozillaBF platform
        #and geriatric master branches that are applicable to that MBF platform
        self.geriatricBranches = geriatricBranches

        MercurialBuildFactory.__init__(self, **kwargs)
        
        if len(self.geriatricMasters) > 0:
            assert self.geriatricBranches
            assert self.platform


    def doUpload(self):
        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_TO_TEMP': '1'})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        # Always upload builds to the dated tinderbox builds directories
        if self.tinderboxBuildsDir is None:
            tinderboxBuildsDir = "%s-%s" % (self.branchName, self.platform)
        else:
            tinderboxBuildsDir = self.tinderboxBuildsDir
        postUploadCmd =  ['post_upload.py']
        postUploadCmd += ['--tinderbox-builds-dir %s' % tinderboxBuildsDir,
                          '-i %(buildid)s',
                          '-p %s' % self.productName,
                          '--release-to-tinderbox-dated-builds']
        if self.nightly:
            # If this is a nightly build also place them in the latest and
            # dated directories in nightly/
            postUploadCmd += ['-b %s' % self.branchName,
                              '--release-to-latest',
                              '--release-to-dated']

        uploadEnv['POST_UPLOAD_CMD'] = WithProperties(' '.join(postUploadCmd))

        def get_url(rc, stdout, stderr):
            for m in re.findall("^(http://.*?\.(?:tar\.bz2|dmg|zip))", "\n".join([stdout, stderr]), re.M):
                if m.endswith("crashreporter-symbols.zip"):
                    continue
                if m.endswith("tests.tar.bz2"):
                    continue
                return {'packageUrl': m}
            return {}

        if self.productName == 'xulrunner':
            self.addStep(SetProperty,
             command=['make', '-f', 'client.mk', 'upload'],
             env=uploadEnv,
             workdir='build',
             extract_fn = get_url,
             haltOnFailure=True,
             description=["upload"]
            )
        else:
            self.addStep(SetProperty,
             command=['make', 'upload'],
             env=uploadEnv,
             workdir='build/%s' % self.objdir,
             extract_fn = get_url,
             haltOnFailure=True,
             description=["upload"]
            )

        talosBranch = "%s-%s" % (self.branchName, self.platform)
        for master, warn in self.talosMasters:
            self.addStep(SendChangeStep(
             name='sendchange_%s' % master,
             warnOnFailure=warn,
             master=master,
             branch=talosBranch,
             revision=WithProperties("%(got_revision)s"),
             files=[WithProperties('%(packageUrl)s')],
             user="sendchange")
            )
        for master, warn, retries in self.unittestMasters:
            self.addStep(SendChangeStep(
             name='sendchange_%s' % master,
             warnOnFailure=warn,
             master=master,
             retries=retries,
             branch=self.unittestBranch,
             revision=WithProperties("%(got_revision)s"),
             files=[WithProperties('%(packageUrl)s')],
             user="sendchange-unittest")
            )
        for master, warn in self.geriatricMasters:
            if self.platform in self.geriatricBranches:
              for branch in self.geriatricBranches[self.platform]:
                self.addStep(SendChangeStep(
                  name='sendchange_geriatric',
                  warnOnFailure=warn,
                  master=master,
                  branch=branch,
                  revision=WithProperties("%(got_revision)s"),
                  files=[WithProperties('%(packageUrl)s')],
                  user='sendchange-geriatric',)
                )
              
            

class CCNightlyBuildFactory(CCMercurialBuildFactory, NightlyBuildFactory):
    def __init__(self, skipBlankRepos=False, mozRepoPath='',
                 inspectorRepoPath='', venkmanRepoPath='',
                 chatzillaRepoPath='', cvsroot='', **kwargs):
        self.skipBlankRepos = skipBlankRepos
        self.mozRepoPath = mozRepoPath
        self.inspectorRepoPath = inspectorRepoPath
        self.venkmanRepoPath = venkmanRepoPath
        self.chatzillaRepoPath = chatzillaRepoPath
        self.cvsroot = cvsroot
        NightlyBuildFactory.__init__(self, mozillaDir='mozilla', **kwargs)



class ReleaseBuildFactory(MercurialBuildFactory):
    def __init__(self, env, version, buildNumber, brandName=None,
            unittestMasters=None, unittestBranch=None, talosMasters=None,
            **kwargs):
        self.version = version
        self.buildNumber = buildNumber

        self.talosMasters = talosMasters or []
        self.unittestMasters = unittestMasters or []
        self.unittestBranch = unittestBranch
        if self.unittestMasters:
            assert self.unittestBranch

        if brandName:
            self.brandName = brandName
        else:
            self.brandName = kwargs['productName'].capitalize()
        # Copy the environment to avoid screwing up other consumers of
        # MercurialBuildFactory
        env = env.copy()
        # Make sure MOZ_PKG_PRETTYNAMES is on and override MOZ_PKG_VERSION
        # The latter is only strictly necessary for RCs.
        env['MOZ_PKG_PRETTYNAMES'] = '1'
        env['MOZ_PKG_VERSION'] = version
        MercurialBuildFactory.__init__(self, env=env, **kwargs)

    def addFilePropertiesSteps(self, filename=None, directory=None, 
                               fileType=None, maxDepth=1, haltOnFailure=False):
        # We don't need to do this for release builds.
        pass    

    def doUpload(self):
        # Make sure the complete MAR has been generated
        self.addStep(ShellCommand,
            name='make_update_pkg',
            command=['make', '-C',
                     '%s/tools/update-packaging' % self.mozillaObjdir],
            env=self.env,
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='echo_buildID',
         command=['bash', '-c',
                  WithProperties('echo buildID=%(buildid)s > ' + \
                                '%s_info.txt' % self.platform)],
         workdir='build/%s/dist' % self.mozillaObjdir
        )

        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_TO_TEMP': '1',
                          'UPLOAD_EXTRA_FILES': '%s_info.txt' % self.platform})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        uploadEnv['POST_UPLOAD_CMD'] = 'post_upload.py ' + \
                                       '-p %s ' % self.productName + \
                                       '-v %s ' % self.version + \
                                       '-n %s ' % self.buildNumber + \
                                       '--release-to-candidates-dir'
        def get_url(rc, stdout, stderr):
            for m in re.findall("^(http://.*?\.(?:tar\.bz2|dmg|zip))", "\n".join([stdout, stderr]), re.M):
                if m.endswith("crashreporter-symbols.zip"):
                    continue
                if m.endswith("tests.tar.bz2"):
                    continue
                return {'packageUrl': m}
            return {'packageUrl': ''}

        self.addStep(SetProperty,
         command=['make', 'upload'],
         env=uploadEnv,
         workdir='build/%s' % self.objdir,
         extract_fn = get_url,
         haltOnFailure=True,
         description=['upload']
        )

        # Send to the "release" branch on talos, it will do
        # super-duper-extra testing
        talosBranch = "%s-release-%s" % (self.branchName, self.platform)
        for master, warn in self.talosMasters:
            self.addStep(SendChangeStep(
             name='sendchange_%s' % master,
             warnOnFailure=warn,
             master=master,
             branch=talosBranch,
             revision=WithProperties("%(got_revision)s"),
             files=[WithProperties('%(packageUrl)s')],
             user="sendchange")
            )

        for master, warn, retries in self.unittestMasters:
            self.addStep(SendChangeStep(
             name='sendchange_%s' % master,
             warnOnFailure=warn,
             master=master,
             retries=retries,
             branch=self.unittestBranch,
             revision=WithProperties("%(got_revision)s"),
             files=[WithProperties('%(packageUrl)s')],
             user="sendchange-unittest")
            )

class CCReleaseBuildFactory(CCMercurialBuildFactory, ReleaseBuildFactory):
    def __init__(self, mozRepoPath='', inspectorRepoPath='',
                 venkmanRepoPath='', chatzillaRepoPath='', cvsroot='',
                 **kwargs):
        self.skipBlankRepos = True
        self.mozRepoPath = mozRepoPath
        self.inspectorRepoPath = inspectorRepoPath
        self.venkmanRepoPath = venkmanRepoPath
        self.chatzillaRepoPath = chatzillaRepoPath
        self.cvsroot = cvsroot
        ReleaseBuildFactory.__init__(self, mozillaDir='mozilla', **kwargs)

    def addFilePropertiesSteps(self, filename=None, directory=None,
                               fileType=None, maxDepth=1, haltOnFailure=False):
        # We don't need to do this for release builds.
        pass


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
    ignore_dirs = MozillaBuildFactory.ignore_dirs + [
            'mozilla-central-macosx-l10n-nightly',
            'mozilla-central-linux-l10n-nightly',
            'mozilla-central-win32-l10n-nightly',
            'mozilla-1.9.1-macosx-l10n-nightly',
            'mozilla-1.9.1-linux-l10n-nightly',
            'mozilla-1.9.1-win32-l10n-nightly',
    ]

    extraConfigureArgs = []

    def __init__(self, project, appName, l10nRepoPath,
                 compareLocalesRepoPath, compareLocalesTag,
                 stageServer, stageUsername, stageSshKey=None,
                 mozconfig=None, configRepoPath=None, configSubDir=None,
                 baseWorkDir='build', tree="notset", mozillaDir=None,
                 l10nTag='default', mergeLocales=True, **kwargs):
        MozillaBuildFactory.__init__(self, **kwargs)

        self.project = project
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
        # WinCE is the only platform that will do repackages with
        # a mozconfig for now. This will be fixed in bug 518359
        if mozconfig and configSubDir and configRepoPath:
            self.mozconfig = 'configs/%s/%s/mozconfig' % (configSubDir,
                                                          mozconfig)
            self.configRepoPath = configRepoPath

        self.addStep(SetBuildProperty(
         property_name='tree',
         value=self.tree,
         haltOnFailure=True
        ))

        # Needed for scratchbox
        self.baseWorkDir = baseWorkDir

        self.origSrcDir = self.branchName

        self.objdir = ''
        if 'MOZ_OBJDIR' in self.env:
            self.objdir = self.env['MOZ_OBJDIR']

        # Mozilla subdir and objdir
        if mozillaDir:
            self.mozillaDir = '/%s' % mozillaDir
            self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, mozillaDir)
        else:
            self.mozillaDir = ''
            if self.objdir is not '':
                self.mozillaSrcDir = '%s/%s' % (self.origSrcDir, self.objdir)
            else:
                self.mozillaSrcDir = self.origSrcDir

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

        self.addStep(ShellCommand,
         name='mkdir_l10nrepopath',
         command=['sh', '-c', 'mkdir -p %s' % self.l10nRepoPath],
         descriptionDone='mkdir '+ self.l10nRepoPath,
         workdir=self.baseWorkDir,
         flunkOnFailure=False
        )

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
        self.doRepack()
        self.doUpload()

    def configure(self):
        self.addStep(ShellCommand,
         name='autoconf',
         command=['bash', '-c', 'autoconf-2.13'],
         haltOnFailure=True,
         descriptionDone=['autoconf'],
         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir)
        )
        if (self.mozillaDir):
            self.addStep(ShellCommand,
             name='autoconf_mozilla',
             command=['bash', '-c', 'autoconf-2.13'],
             haltOnFailure=True,
             descriptionDone=['autoconf mozilla'],
             workdir='%s/%s' % (self.baseWorkDir, self.mozillaSrcDir)
            )
        self.addStep(ShellCommand,
         name='autoconf_js_src',
         command=['bash', '-c', 'autoconf-2.13'],
         haltOnFailure=True,
         descriptionDone=['autoconf js/src'],
         workdir='%s/%s/js/src' % (self.baseWorkDir, self.origSrcDir)
        )
        # WinCE is the only platform that will do repackages with
        # a mozconfig for now. This will be fixed in bug 518359
        if self.platform is 'wince':
            self.addStep(ShellCommand,
             name='configure',
             command=['make -f client.mk configure'], 
             description='configure',
             descriptionDone='configure done',
             haltOnFailure=True,
             env = self.env,
             workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir)
            )
        else:
            # For backward compatibility where there is no mozconfig
            self.addStep(ShellCommand,
             name='configure',
             command=['sh', '--',
                      './configure', '--enable-application=%s' % self.appName,
                      '--with-l10n-base=../%s' % self.l10nRepoPath ] +
                      self.extraConfigureArgs,
             description='configure',
             descriptionDone='configure done',
             haltOnFailure=True,
             workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir)
            )
        self.addStep(ShellCommand,
         name='make_config',
         command=['make'],
         workdir='%s/%s/config' % (self.baseWorkDir, self.mozillaSrcDir),
         description=['make config'],
         haltOnFailure=True
        )

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

    def doUpload(self):
        self.addStep(ShellCommand,
         name='make_l10n_upload',
         command=['make', WithProperties('l10n-upload-%(locale)s')],
         env=self.uploadEnv,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.mozillaSrcDir,
                                       self.appName),
         haltOnFailure=True,
         flunkOnFailure=True
        )

    def getSources(self):
        self.addStep(ShellCommand,
         name='get_enUS_src',
         command=['sh', '-c',
          WithProperties('if [ -d '+self.origSrcDir+' ]; then ' +
                         'hg -R '+self.origSrcDir+' pull -r default ;'+
                         'else ' +
                         'hg clone ' +
                         'http://'+self.hgHost+'/'+self.repoPath+' ' +
                         self.origSrcDir+' ; ' +
                         'fi ' +
                         '&& hg -R '+self.origSrcDir+' update -r %(en_revision)s')],
         descriptionDone="en-US source",
         workdir=self.baseWorkDir,
         haltOnFailure=True,
         timeout=30*60 # 30 minutes
        )
        self.addStep(ShellCommand,
         name='get_locale_src',
         command=['sh', '-c',
          WithProperties('if [ -d %(locale)s ]; then ' +
                         'hg -R %(locale)s pull -r default ; ' +
                         'else ' +
                         'hg clone ' +
                         'http://'+self.hgHost+'/'+self.l10nRepoPath+\
                           '/%(locale)s/ ; ' +
                         'fi ' +
                         '&& hg -R %(locale)s update -r %(l10n_revision)s')],
         descriptionDone="locale source",
         timeout=5*60, # 5 minutes
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.l10nRepoPath)
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
        self.addStep(ShellCommand,
         name='rm_compare_locales',
         command=['rm', '-rf', 'compare-locales'],
         description=['remove', 'compare-locales'],
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='clone_compare_locales',
         command=['hg', 'clone', compareLocalesRepo, 'compare-locales'],
         description=['checkout', 'compare-locales'],
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='update_compare_locales',
         command=['hg', 'up', '-C', '-r', self.compareLocalesTag],
         description='update compare-locales',
         workdir='%s/compare-locales' % self.baseWorkDir,
         haltOnFailure=True
        )

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
        self.addStep(ShellCommand,
         name='rm_merged',
         command=['rm', '-rf', 'merged'],
         description=['remove', 'merged'],
         workdir="%s/%s/%s/locales" % (self.baseWorkDir,
                                       self.origSrcDir,
                                       self.appName),
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
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
        )

    def doRepack(self):
        '''Perform the repackaging.

        This is implemented in the subclasses.
        '''
        pass

    def preClean(self):
        self.addStep(ShellCommand,
         name='rm_dist_upload',
         command=['sh', '-c',
                  'if [ -d '+self.mozillaSrcDir+'/dist/upload ]; then ' +
                  'rm -rf '+self.mozillaSrcDir+'/dist/upload; ' +
                  'fi'],
         description="rm dist/upload",
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )

        self.addStep(ShellCommand,
         name='rm_dist_update',
         command=['sh', '-c',
                  'if [ -d '+self.branchName+'/dist/update ]; then ' +
                  'rm -rf '+self.branchName+'/dist/update; ' +
                  'fi'],
         description="rm dist/update",
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )

class CCBaseRepackFactory(BaseRepackFactory):
    # Override ignore_dirs so that we don't delete l10n nightly builds
    # before running a l10n nightly build
    ignore_dirs = MozillaBuildFactory.ignore_dirs + [
            'comm-central-macosx-l10n-nightly',
            'comm-central-linux-l10n-nightly',
            'comm-central-win32-l10n-nightly',
            'comm-central-trunk-macosx-l10n-nightly',
            'comm-central-trunk-linux-l10n-nightly',
            'comm-central-trunk-win32-l10n-nightly',
            'comm-1.9.1-macosx-l10n-nightly',
            'comm-1.9.1-linux-l10n-nightly',
            'comm-1.9.1-win32-l10n-nightly',
    ]

    def __init__(self, skipBlankRepos=False, mozRepoPath='',
                 inspectorRepoPath='', venkmanRepoPath='',
                 chatzillaRepoPath='', cvsroot='', buildRevision='',
                 **kwargs):
        self.skipBlankRepos = skipBlankRepos
        self.mozRepoPath = mozRepoPath
        self.inspectorRepoPath = inspectorRepoPath
        self.venkmanRepoPath = venkmanRepoPath
        self.chatzillaRepoPath = chatzillaRepoPath
        self.cvsroot = cvsroot
        self.buildRevision = buildRevision
        BaseRepackFactory.__init__(self, mozillaDir='mozilla', **kwargs)

    def getSources(self):
        BaseRepackFactory.getSources(self)
        # build up the checkout command with all options
        co_command = ['python', 'client.py', 'checkout',
                      WithProperties('--comm-rev=%(en_revision)s')]
        if self.mozRepoPath:
            co_command.append('--mozilla-repo=%s' % self.getRepository(self.mozRepoPath))
        if self.inspectorRepoPath:
            co_command.append('--inspector-repo=%s' % self.getRepository(self.inspectorRepoPath))
        elif self.skipBlankRepos:
            co_command.append('--skip-inspector')
        if self.venkmanRepoPath:
            co_command.append('--venkman-repo=%s' % self.getRepository(self.venkmanRepoPath))
        elif self.skipBlankRepos:
            co_command.append('--skip-venkman')
        if self.chatzillaRepoPath:
            co_command.append('--chatzilla-repo=%s' % self.getRepository(self.chatzillaRepoPath))
        elif self.skipBlankRepos:
            co_command.append('--skip-chatzilla')
        if self.cvsroot:
            co_command.append('--cvsroot=%s' % self.cvsroot)
        if self.buildRevision:
            co_command.append('--comm-rev=%s' % self.buildRevision)
            co_command.append('--mozilla-rev=%s' % self.buildRevision)
            co_command.append('--inspector-rev=%s' % self.buildRevision)
            co_command.append('--venkman-rev=%s' % self.buildRevision)
            co_command.append('--chatzilla-rev=%s' % self.buildRevision)
        # execute the checkout
        self.addStep(ShellCommand,
         command=co_command,
         description=['running', 'client.py', 'checkout'],
         descriptionDone=['client.py', 'checkout'],
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir),
         timeout=60*60 # 1 hour
        )

class NightlyRepackFactory(BaseRepackFactory):
    extraConfigureArgs = []
    
    def __init__(self, enUSBinaryURL, nightly=False, env={}, platform='', 
                 configRepoPath=None, configSubDir=None, mozconfig=None,
                 ausBaseUploadDir=None, updatePlatform=None,
                 downloadBaseURL=None, ausUser=None, ausHost=None,
                 l10nNightlyUpdate=False, l10nDatedDirs=False, **kwargs):
        self.nightly = nightly
        self.env = env.copy()
        self.platform = platform
        self.l10nNightlyUpdate = l10nNightlyUpdate
        self.ausBaseUploadDir = ausBaseUploadDir
        self.updatePlatform = updatePlatform
        self.downloadBaseURL = downloadBaseURL
        self.ausUser = ausUser
        self.ausHost = ausHost
        self.env.update({'EN_US_BINARY_URL':enUSBinaryURL})
        self.configRepoPath = configRepoPath
        self.configSubDir = configSubDir
        self.mozconfig = mozconfig
        if mozconfig:
            self.configRepo = self.getRepository(self.configRepoPath,
                                                 kwargs['hgHost'])
            self.mozconfig = 'configs/%s/%s/mozconfig' % (self.configSubDir,
                                                          mozconfig)
            # I need to use L10nBASEDIR in combination with "make -f client.mk"
            # It is relative to env['MOZ_OBJDIR'] which is specified in config.py
            self.env.update({'L10NBASEDIR':  '../../%s' % kwargs['l10nRepoPath']})

        # Unfortunately, we can't call BaseRepackFactory.__init__() before this
        # because it needs self.postUploadCmd set
        assert 'project' in kwargs
        assert 'repoPath' in kwargs

        # 1) upload preparation
        if 'branchName' in kwargs:
          uploadDir = '%s-l10n' % kwargs['branchName']
        else:
          uploadDir = '%s-l10n' % self.getRepoName(kwargs['repoPath'])
        postUploadCmd = ['post_upload.py', 
                         '-p %s ' % kwargs['project'],
                         '-b %s ' % uploadDir]
        if l10nDatedDirs:
            # nightly repacks and on-change upload to different places
            if self.nightly:
                postUploadCmd += ['--buildid %(buildid)s',
                                  '--release-to-latest',
                                  '--release-to-dated']
            else:
                # For the repack-on-change scenario we just want to upload
                # to tinderbox builds
                postUploadCmd += \
                      ['--tinderbox-builds-dir %s' % uploadDir,
                      '--release-to-tinderbox-builds']
            self.postUploadCmd = WithProperties(' '.join(postUploadCmd))
        else:
            # for backwards compatibility when the nightly and repack on-change
            # runs were the same 
            postUploadCmd += ['--release-to-latest']
            self.postUploadCmd = ' '.join(postUploadCmd)

        # 2) preparation for updates
        if l10nNightlyUpdate and self.nightly:
            self.env.update({'MOZ_MAKE_COMPLETE_MAR': '1', 
                             'DOWNLOAD_BASE_URL': '%s/nightly' % self.downloadBaseURL})
            self.extraConfigureArgs = ['--enable-update-packaging']


        BaseRepackFactory.__init__(self, **kwargs)
        if l10nNightlyUpdate:
            assert ausBaseUploadDir and updatePlatform and downloadBaseURL
            assert ausUser and ausHost

            # this is a tad ugly because we need to python interpolation
            # as well as WithProperties
            # here's an example of what it translates to:
            # /opt/aus2/build/0/Firefox/mozilla-central/WINNT_x86-msvc/2008010103/fr
            self.ausFullUploadDir = '%s/%s/%%(buildid)s/%%(locale)s' % \
              (self.ausBaseUploadDir, self.updatePlatform)
            self.env.update({'FILE_BRANCH': self.branchName})
            self.createNightlySnippet()
            self.uploadSnippet()

    def updateSources(self):
        self.addStep(ShellCommand,
         name='update_locale_source',
         command=['hg', 'up', '-C', '-r', self.l10nTag],
         description='update workdir',
         workdir=WithProperties('build/' + self.l10nRepoPath + '/%(locale)s'),
         haltOnFailure=True
        )
        self.addStep(SetProperty,
                     command=['hg', 'ident', '-i'],
                     haltOnFailure=True,
                     property='l10n_revision',
                     workdir=WithProperties('build/' + self.l10nRepoPath + 
                                            '/%(locale)s')
                     )

    def getMozconfig(self):
        if self.mozconfig and self.mozconfig != '':
            # TODO These steps are exactly the same as what the ReleaseRepackFactory calls.
            # These should be moved to the parent BaseRepackFactory in bug 537287 
            self.addStep(ShellCommand,
             name='rm_configs',
             command=['rm', '-rf', 'configs'],
             description=['remove', 'configs'],
             workdir='build/'+self.origSrcDir,
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             name='checkout_configs',
             command=['hg', 'clone', self.configRepo, 'configs'],
             description=['checkout', 'configs'],
             workdir='build/'+self.origSrcDir,
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             # cp configs/mozilla2/$platform/$branchame/$type/mozconfig .mozconfig
             name='copy_mozconfig',
             command=['cp', self.mozconfig, '.mozconfig'],
             description=['copy mozconfig'],
             workdir='build/'+self.origSrcDir,
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             name='cat_mozconfig',
             command=['cat', '.mozconfig'],
             workdir='build/'+self.origSrcDir
            )

    def downloadBuilds(self):
        self.addStep(ShellCommand,
         name='wget_enUS',
         command=['make', 'wget-en-US'],
         descriptionDone='wget en-US',
         env=self.env,
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.mozillaSrcDir, self.appName)
        )

    def updateEnUS(self):
        '''Update en-US to the source stamp we get from make ident.

        Requires that we run make unpack first.
        '''
        self.addStep(ShellCommand,
                     name='make_unpack',
                     command=['make', 'unpack'],
                     descriptionDone='unpacked en-US',
                     haltOnFailure=True,
                     workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.mozillaSrcDir, self.appName),
                     )
        self.addStep(SetProperty,
                     command=['make', 'ident'],
                     haltOnFailure=True,
                     workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.mozillaSrcDir, self.appName),
                     extract_fn=identToProperties('fx_revision')
                     )
        self.addStep(ShellCommand,
                     name='update_enUS_revision',
                     command=['hg', 'update', '-r',
                              WithProperties('%(fx_revision)s')],
                     haltOnFailure=True,
                     workdir='build/' + self.origSrcDir)

    def tinderboxPrintRevisions(self):
        self.tinderboxPrint('fx_revision',WithProperties('%(fx_revision)s'))
        self.tinderboxPrint('l10n_revision',WithProperties('%(l10n_revision)s'))

    def doRepack(self):
        # wince needs this step for nsprpub to succeed
        if self.platform is 'wince':
            self.addStep(ShellCommand,
             name='make_build',
             command=['make'],
             workdir='%s/%s/build' % (self.baseWorkDir, self.mozillaSrcDir),
             description=['make build'],
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         name='make_nsprpub',
         command=['make'],
         workdir='%s/%s/nsprpub' % (self.baseWorkDir, self.mozillaSrcDir),
         description=['make nsprpub'],
         haltOnFailure=True
        )
        if self.l10nNightlyUpdate:
            # Because we're generating updates we need to build the libmar tools
            self.addStep(ShellCommand,
             command=['make'],
             workdir='%s/%s/modules/libmar' % (self.baseWorkDir, self.mozillaSrcDir),
             description=['make', 'modules/libmar'],
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         name='make_locale_installers',
         command=['sh','-c',
                  WithProperties('make installers-%(locale)s LOCALE_MERGEDIR=$PWD/merged')],
         env = self.env,
         haltOnFailure=True,
         description=['make','installers'],
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.mozillaSrcDir, self.appName),
        )

    def createNightlySnippet(self):
        self.addStep(ShellCommand,
         name='make_locale_snippet',
         command=['sh','-c',
                  WithProperties('make generate-snippet-%(locale)s')],
         env = self.env,
         haltOnFailure=True,
         description=['make','snippet'],
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.mozillaSrcDir, self.appName),
        )

    def uploadSnippet(self):
        self.addStep(ShellCommand,
         command=['ssh', '-l', self.ausUser, self.ausHost,
                  WithProperties('mkdir -p %s' % self.ausFullUploadDir)],
         description=['create', 'aus', 'upload', 'dir'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['scp', '-o', 'User=%s' % self.ausUser,
                  'dist/update/complete.update.snippet',
                  WithProperties('%s:%s/complete.txt' % \
                    (self.ausHost, self.ausFullUploadDir))],
         workdir='build/%s' % self.mozillaSrcDir,
         description=['upload', 'complete', 'snippet'],
         haltOnFailure=True
        )

class CCNightlyRepackFactory(CCBaseRepackFactory, NightlyRepackFactory):
    def __init__(self, skipBlankRepos=False, mozRepoPath='',
                 inspectorRepoPath='', venkmanRepoPath='',
                 chatzillaRepoPath='', cvsroot='', buildRevision='',
                 **kwargs):
        self.skipBlankRepos = skipBlankRepos
        self.mozRepoPath = mozRepoPath
        self.inspectorRepoPath = inspectorRepoPath
        self.venkmanRepoPath = venkmanRepoPath
        self.chatzillaRepoPath = chatzillaRepoPath
        self.cvsroot = cvsroot
        self.buildRevision = buildRevision
        NightlyRepackFactory.__init__(self, mozillaDir='mozilla', **kwargs)

    # it sucks to override all of updateEnUS but we need to do it that way
    # this is basically mirroring what mobile does
    def updateEnUS(self):
        '''Update en-US to the source stamp we get from make ident.

        Requires that we run make unpack first.
        '''
        self.addStep(ShellCommand,
         command=['make', 'unpack'],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName)
        )
        self.addStep(SetProperty,
         command=['make', 'ident'],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName),
         extract_fn=identToProperties()
        )
        self.addStep(ShellCommand,
         command=['hg', 'update', '-r', WithProperties('%(comm_revision)s')],
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir)
        )
        self.addStep(ShellCommand,
         command=['hg', 'update', '-r', WithProperties('%(moz_revision)s')],
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.mozillaSrcDir)
        )

    def tinderboxPrintRevisions(self):
        self.tinderboxPrint('comm_revision',WithProperties('%(comm_revision)s'))
        self.tinderboxPrint('moz_revision',WithProperties('%(moz_revision)s'))
        self.tinderboxPrint('l10n_revision',WithProperties('%(l10n_revision)s'))

    # unsure why we need to explicitely do this but after bug 478436 we stopped
    # executing the actual repackaging without this def here
    def doRepack(self):
        NightlyRepackFactory.doRepack(self)


class ReleaseFactory(MozillaBuildFactory):
    def getCandidatesDir(self, product, version, buildNumber):
        # can be used with rsync, eg host + ':' + getCandidatesDir()
        # and "http://' + host + getCandidatesDir()
        return '/pub/mozilla.org/' + product + '/nightly/' + str(version) + \
               '-candidates/build' + str(buildNumber) + '/'

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



class ReleaseRepackFactory(BaseRepackFactory, ReleaseFactory):
    def __init__(self, configRepoPath, configSubDir, mozconfig, platform,
                 buildRevision, version, buildNumber, brandName=None, **kwargs):
        self.configRepoPath = configRepoPath
        self.configSubDir = configSubDir
        self.platform = platform
        self.buildRevision = buildRevision
        self.version = version
        self.buildNumber = buildNumber
        if brandName:
            self.brandName = brandName
        else:
            self.brandName = kwargs['project'].capitalize()
        # more vars are added in downloadBuilds
        self.env = {'MOZ_PKG_PRETTYNAMES': '1',
                    'MOZ_PKG_VERSION': self.version,
                    'MOZ_MAKE_COMPLETE_MAR': '1'}

        self.configRepo = self.getRepository(self.configRepoPath,
                                             kwargs['hgHost'])

        self.mozconfig = 'configs/%s/%s/mozconfig' % (configSubDir, mozconfig)

        assert 'project' in kwargs
        # TODO: better place to put this/call this
        self.postUploadCmd = 'post_upload.py ' + \
                             '-p %s ' % kwargs['project'] + \
                             '-v %s ' % self.version + \
                             '-n %s ' % self.buildNumber + \
                             '--release-to-candidates-dir'
        BaseRepackFactory.__init__(self, **kwargs)

    def updateSources(self):
        self.addStep(ShellCommand,
         name='update_sources',
         command=['hg', 'up', '-C', '-r', self.buildRevision],
         workdir='build/'+self.origSrcDir,
         description=['update %s' % self.branchName,
                      'to %s' % self.buildRevision],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='update_locale_sources',
         command=['hg', 'up', '-C', '-r', self.buildRevision],
         workdir=WithProperties('build/' + self.l10nRepoPath + '/%(locale)s'),
         description=['update to', self.buildRevision]
        )
        self.addStep(SetProperty,
                     command=['hg', 'ident', '-i'],
                     haltOnFailure=True,
                     property='l10n_revision',
                     workdir=WithProperties('build/' + self.l10nRepoPath + 
                                            '/%(locale)s')
                     )

    def getMozconfig(self):
        self.addStep(ShellCommand,
         name='rm_configs',
         command=['rm', '-rf', 'configs'],
         description=['remove', 'configs'],
         workdir='build/'+self.origSrcDir,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='checkout_configs',
         command=['hg', 'clone', self.configRepo, 'configs'],
         description=['checkout', 'configs'],
         workdir='build/'+self.origSrcDir,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         # cp configs/mozilla2/$platform/$branchame/$type/mozconfig .mozconfig
         name='copy_mozconfig',
         command=['cp', self.mozconfig, '.mozconfig'],
         description=['copy mozconfig'],
         workdir='build/'+self.origSrcDir,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='cat_mozconfig',
         command=['cat', '.mozconfig'],
         workdir='build/'+self.origSrcDir
        )

    def downloadBuilds(self):
        # We need to know the absolute path to the input builds when we repack,
        # so we need retrieve at run-time as a build property
        self.addStep(SetProperty,
         command=['bash', '-c', 'pwd'],
         property='srcdir',
         workdir='build/'+self.origSrcDir
        )

        candidatesDir = 'http://%s' % self.stageServer + \
                        '/pub/mozilla.org/%s/nightly' % self.project + \
                        '/%s-candidates/build%s' % (self.version,
                                                    self.buildNumber)
        longVersion = self.makeLongVersion(self.version)

        # This block sets platform specific data that our wget command needs.
        #  build is mapping between the local and remote filenames
        #  platformDir is the platform specific directory builds are contained
        #    in on the stagingServer.
        # This block also sets the necessary environment variables that the
        # doRepack() steps rely on to locate their source build.
        builds = {}
        platformDir = None
        if self.platform.startswith('linux'):
            platformDir = 'linux-i686'
            filename = '%s.tar.bz2' % self.project
            builds[filename] = '%s-%s.tar.bz2' % (self.project, self.version)
            self.env['ZIP_IN'] = WithProperties('%(srcdir)s/' + filename)
        elif self.platform.startswith('macosx'):
            platformDir = 'mac'
            filename = '%s.dmg' % self.project
            builds[filename] = '%s %s.dmg' % (self.brandName,
                                              longVersion)
            self.env['ZIP_IN'] = WithProperties('%(srcdir)s/' + filename)
        elif self.platform.startswith('win32'):
            platformDir = 'unsigned/win32'
            filename = '%s.zip' % self.project
            instname = '%s.exe' % self.project
            builds[filename] = '%s-%s.zip' % (self.project, self.version)
            builds[instname] = '%s Setup %s.exe' % (self.brandName,
                                                    longVersion)
            self.env['ZIP_IN'] = WithProperties('%(srcdir)s/' + filename)
            self.env['WIN32_INSTALLER_IN'] = \
              WithProperties('%(srcdir)s/' + instname)
        else:
            raise "Unsupported platform"

        for name in builds:
            self.addStep(ShellCommand,
             name='get_candidates_%s' % name,
             command=['wget', '-O', name, '--no-check-certificate',
                      '%s/%s/en-US/%s' % (candidatesDir, platformDir,
                                          builds[name])],
             workdir='build/'+self.origSrcDir,
             haltOnFailure=True
            )

    def compareLocales(self):
        self.addStep(ShellCommand,
         name='comparing_locales',
         command=['python',
                  '../../../compare-locales/scripts/compare-locales',
                  "l10n.ini",
                  "../../../%s" % self.l10nRepoPath,
                  WithProperties('%(locale)s')],
         description='comparing locale',
         env={'PYTHONPATH': ['../../../compare-locales/lib']},
         flunkOnWarnings=True,
         haltOnFailure=True,
         workdir="%s/%s/%s/locales" % (self.baseWorkDir,
                                       self.origSrcDir,
                                       self.appName),
        )

    def doRepack(self):
        # For releases we have to make memory/jemalloc
        if self.platform.startswith('win32'):
            self.addStep(ShellCommand,
             name='make_memory_jemalloc',
             command=['make'],
             workdir='build/'+self.mozillaSrcDir+'/memory/jemalloc',
             description=['make memory/jemalloc'],
             haltOnFailure=True
            )
        # Because we're generating updates we need to build the libmar tools
        for dir in ('nsprpub', 'modules/libmar'):
            self.addStep(ShellCommand,
             name='make_%s' % dir,
             command=['make'],
             workdir='build/'+self.mozillaSrcDir+'/'+dir,
             description=['make ' + dir],
             haltOnFailure=True
            )

        self.addStep(ShellCommand,
         name='make_installers_locale',
         command=['make', WithProperties('installers-%(locale)s')],
         env=self.env,
         haltOnFailure=True,
         workdir='build/'+self.origSrcDir+'/'+self.appName+'/locales'
        )

class CCReleaseRepackFactory(CCBaseRepackFactory, ReleaseRepackFactory):
    def __init__(self, mozRepoPath='', inspectorRepoPath='',
                 venkmanRepoPath='', chatzillaRepoPath='', cvsroot='',
                 **kwargs):
        self.skipBlankRepos = True
        self.mozRepoPath = mozRepoPath
        self.inspectorRepoPath = inspectorRepoPath
        self.venkmanRepoPath = venkmanRepoPath
        self.chatzillaRepoPath = chatzillaRepoPath
        self.cvsroot = cvsroot
        ReleaseRepackFactory.__init__(self, mozillaDir='mozilla', **kwargs)

    def updateSources(self):
        ReleaseRepackFactory.updateSources(self)
        self.addStep(ShellCommand,
         command=['hg', 'up', '-C', '-r', self.buildRevision],
         workdir='build/'+self.mozillaSrcDir,
         description=['update mozilla',
                      'to %s' % self.buildRevision],
         haltOnFailure=True
        )
        if self.venkmanRepoPath:
            self.addStep(ShellCommand,
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             workdir='build/'+self.mozillaSrcDir+'/extensions/venkman',
             description=['update venkman',
                          'to %s' % self.buildRevision],
             haltOnFailure=True
            )
        if self.inspectorRepoPath:
            self.addStep(ShellCommand,
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             workdir='build/'+self.mozillaSrcDir+'/extensions/inspector',
             description=['update inspector',
                          'to %s' % self.buildRevision],
             haltOnFailure=True
            )
        if self.chatzillaRepoPath:
            self.addStep(ShellCommand,
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             workdir='build/'+self.mozillaSrcDir+'/extensions/irc',
             description=['update chatzilla',
                          'to %s' % self.buildRevision],
             haltOnFailure=True
            )

    def downloadBuilds(self):
        ReleaseRepackFactory.downloadBuilds(self)

    # unsure why we need to explicitely do this but after bug 478436 we stopped
    # executing the actual repackaging without this def here
    def doRepack(self):
        ReleaseRepackFactory.doRepack(self)


class StagingRepositorySetupFactory(ReleaseFactory):
    """This Factory should be run at the start of a staging release run. It
       deletes and reclones all of the repositories in 'repositories'. Note that
       the staging buildTools repository should _not_ be recloned, as it is
       used by many other builders, too.
    """
    def __init__(self, username, sshKey, repositories, **kwargs):
        # MozillaBuildFactory needs the 'repoPath' argument, but we don't
        ReleaseFactory.__init__(self, repoPath='nothing', **kwargs)
        for repoPath in sorted(repositories.keys()):
            repo = self.getRepository(repoPath)
            pushRepo = self.getRepository(repoPath, push=True)
            repoName = self.getRepoName(repoPath)

            # test for existence
            command = 'wget -O- %s >/dev/null' % repo
            command += ' && '
            # if it exists, delete it
            command += 'ssh -l %s -i %s %s edit %s delete YES' % \
              (username, sshKey, self.hgHost, repoName)
            command += '; '
            # either way, try to create it again
            # this kindof sucks, but if we '&&' we can't create repositories
            # that don't already exist, which is a huge pain when adding new
            # locales or repositories.
            command += 'ssh -l %s -i %s %s clone %s %s' % \
              (username, sshKey, self.hgHost, repoName, repoPath)

            self.addStep(ShellCommand,
             name='recreate_repo',
             command=['bash', '-c', command],
             description=['recreate', repoName],
             timeout=30*60 # 30 minutes
            )



class ReleaseTaggingFactory(ReleaseFactory):
    def __init__(self, repositories, productName, appName, version, appVersion,
                 milestone, baseTag, buildNumber, hgUsername, hgSshKey=None,
                 relbranchPrefix=None, buildSpace=1.5, **kwargs):
        """Repositories looks like this:
            repositories[name]['revision']: changeset# or tag
            repositories[name]['relbranchOverride']: branch name
            repositories[name]['bumpFiles']: [filesToBump]
           eg:
            repositories['http://hg.mozilla.org/mozilla-central']['revision']:
              d6a0a4fca081
            repositories['http://hg.mozilla.org/mozilla-central']['relbranchOverride']:
              GECKO191_20080828_RELBRANCH
            repositories['http://hg.mozilla.org/mozilla-central']['bumpFiles']:
              ['client.mk', 'browser/config/version.txt',
               'js/src/config/milestone.txt', 'config/milestone.txt']
            relbranchOverride is typically used in two situations:
             1) During a respin (buildNumber > 1) when the "release" branch has
                already been created (during build1). In cases like this all
                repositories should have the relbranch specified
             2) During non-Firefox builds. Because Seamonkey, Thunderbird, etc.
                are releases off of the same platform code as Firefox, the
                "release branch" will already exist in mozilla-central but not
                comm-central, mobile-browser, domi, etc. In cases like this,
                mozilla-central and l10n should be specified with a
                relbranchOverride and the other source repositories should NOT
                specify one.
           productName: The name of the actual *product* being shipped.
                        Examples include: firefox, thunderbird, seamonkey.
                        This is only used for the automated check-in message
                        the version bump generates.
           appName: The "application" name (NOT product name). Examples:
                    browser, suite, mailnews. It is used in version bumping
                    code and assumed to be a subdirectory of the source
                    repository being bumped. Eg, for Firefox, appName should be
                    'browser', which is a subdirectory of 'mozilla-central'.
                    For Thunderbird, it would be 'mailnews', a subdirectory
                    of 'comm-central'.
           version: What this build is actually called. I most cases this is
                    the version number of the application, eg, 3.0.6, 3.1b2.
                    During the RC phase we "call" builds, eg, 3.1 RC1, but the
                    version of the application is still 3.1. In these cases,
                    version should be set to, eg, 3.1rc1.
           appVersion: The current version number of the application being
                       built. Eg, 3.0.2 for Firefox, 2.0 for Seamonkey, etc.
                       This is different than the platform version. See below.
                       This is usually the same as 'version', except during the
                       RC phase. Eg, when version is 3.1rc1 appVersion is still
                       3.1.
           milestone: The current version of *Gecko*. This is generally
                      along the lines of: 1.8.1.14, 1.9.0.2, etc.
           baseTag: The prefix to use for BUILD/RELEASE tags. It will be
                    post-fixed with _BUILD$buildNumber and _RELEASE. Generally,
                    this is something like: FIREFOX_3_0_2.
           buildNumber: The current build number. If this is the first time
                        attempting a release this is 1. Other times it may be
                        higher. It is used for post-fixing tags and some
                        internal logic.
           hgUsername: The username to use when pushing changes to the remote
                       repository.
           hgSshKey: The full path to the ssh key to use (if necessary) when
                     pushing changes to the remote repository.
           relbranchPrefix: the prefix to start relelease branch names with
                            (defaults to 'GECKO')

        """
        # MozillaBuildFactory needs the 'repoPath' argument, but we don't
        ReleaseFactory.__init__(self, repoPath='nothing', buildSpace=buildSpace,
                                **kwargs)

        # extremely basic validation, to catch really dumb configurations
        assert len(repositories) > 0, \
          'You must provide at least one repository.'
        assert productName, 'You must provide a product name (eg. firefox).'
        assert appName, 'You must provide an application name (eg. browser).'
        assert version, \
          'You must provide an application version (eg. 3.0.2).'
        assert milestone, 'You must provide a milestone (eg. 1.9.0.2).'
        assert baseTag, 'You must provide a baseTag (eg. FIREFOX_3_0_2).'
        assert buildNumber, 'You must provide a buildNumber.'

        # if we're doing a respin we already have a relbranch created
        if buildNumber > 1:
            for repo in repositories:
                assert repositories[repo]['relbranchOverride'], \
                  'No relbranchOverride specified for ' + repo + \
                  '. You must provide a relbranchOverride when buildNumber > 1'

        # now, down to work
        self.buildTag = '%s_BUILD%s' % (baseTag, str(buildNumber))
        self.releaseTag = '%s_RELEASE' % baseTag

        # generate the release branch name, which is based on the
        # version and the current date.
        # looks like: GECKO191_20080728_RELBRANCH
        # This can be overridden per-repository. This case is handled
        # in the loop below
        if not relbranchPrefix:
            relbranchPrefix = 'GECKO'
        relbranchName = '%s%s_%s_RELBRANCH' % (
          relbranchPrefix, milestone.replace('.', ''),
          datetime.now().strftime('%Y%m%d'))

        for repoPath in sorted(repositories.keys()):
            repoName = self.getRepoName(repoPath)
            repo = self.getRepository(repoPath)
            pushRepo = self.getRepository(repoPath, push=True)

            sshKeyOption = self.getSshKeyOption(hgSshKey)

            repoRevision = repositories[repoPath]['revision']
            bumpFiles = repositories[repoPath]['bumpFiles']

            # use repo-specific variable so that a changed name doesn't
            # propagate to later repos without an override
            relbranchOverride = False
            if repositories[repoPath]['relbranchOverride']:
                relbranchOverride = True
                repoRelbranchName = repositories[repoPath]['relbranchOverride']
            else:
                repoRelbranchName = relbranchName

            # For l10n we never bump any files, so this will never get
            # overridden. For source repos, we will do a version bump in build1
            # which we commit, and set this property again, so we tag
            # the right revision. For build2, we don't version bump, and this
            # will not get overridden
            self.addStep(SetBuildProperty(
             property_name="%s-revision" % repoName,
             value=repoRevision,
             haltOnFailure=True
            ))
            # 'hg clone -r' breaks in the respin case because the cloned
            # repository will not have ANY changesets from the release branch
            # and 'hg up -C' will fail
            self.addStep(ShellCommand,
             name='hg_clone',
             command=['hg', 'clone', repo, repoName],
             workdir='.',
             description=['clone %s' % repoName],
             haltOnFailure=True,
             timeout=30*60 # 30 minutes
            )
            # for build1 we need to create a branch
            if buildNumber == 1 and not relbranchOverride:
                # remember:
                # 'branch' in Mercurial does not actually create a new branch,
                # it switches the "current" branch to the one of the given name.
                # when buildNumber == 1 this will end up creating a new branch
                # when we commit version bumps and tags.
                # note: we don't actually have to switch to the release branch
                # to create tags, but it seems like a more sensible place to
                # have those commits
                self.addStep(ShellCommand,
                 name='hg_update',
                 command=['hg', 'up', '-r',
                          WithProperties('%s', '%s-revision' % repoName)],
                 workdir=repoName,
                 description=['update', repoName],
                 haltOnFailure=True
                )
                self.addStep(ShellCommand,
                 name='hg_branch',
                 command=['hg', 'branch', repoRelbranchName],
                 workdir=repoName,
                 description=['branch %s' % repoName],
                 haltOnFailure=True
                )
            # if buildNumber > 1 we need to switch to it with 'hg up -C'
            else:
                self.addStep(ShellCommand,
                 name='switch_branch',
                 command=['hg', 'up', '-C', repoRelbranchName],
                 workdir=repoName,
                 description=['switch to', repoRelbranchName],
                 haltOnFailure=True
                )
            # we don't need to do any version bumping if this is a respin
            if buildNumber == 1 and len(bumpFiles) > 0:
                command = ['perl', 'tools/release/version-bump.pl',
                           '-w', repoName, '-t', self.releaseTag, '-a', appName,
                           '-v', appVersion, '-m', milestone]
                command.extend(bumpFiles)
                self.addStep(ShellCommand,
                 name='bump',
                 command=command,
                 workdir='.',
                 description=['bump %s' % repoName],
                 haltOnFailure=True
                )
                self.addStep(ShellCommand,
                 name='hg_diff',
                 command=['hg', 'diff'],
                 workdir=repoName
                )
                self.addStep(ShellCommand,
                 # mozilla-central and other developer repositories have a
                 # 'CLOSED TREE' hook on them which rejects commits when the
                 # tree is declared closed. It is very common for us to tag
                 # and branch when the tree is in this state. Adding the
                 # 'CLOSED TREE' string at the end will force the hook to
                 # let us commit regardless of the tree state.
                 name='hg_commit',
                 command=['hg', 'commit', '-u', hgUsername, '-m',
                          'Automated checkin: version bump remove "pre" ' + \
                          ' from version number for ' + productName + ' ' + \
                          version + ' release on ' + repoRelbranchName + ' ' + \
                          'CLOSED TREE'],
                 workdir=repoName,
                 description=['commit %s' % repoName],
                 haltOnFailure=True
                )
                self.addStep(SetProperty,
                 command=['hg', 'identify', '-i'],
                 property='%s-revision' % repoName,
                 workdir=repoName,
                 haltOnFailure=True
                )
            for tag in (self.buildTag, self.releaseTag):
                self.addStep(ShellCommand,
                 name='hg_tag',
                 command=['hg', 'tag', '-u', hgUsername, '-f', '-r',
                          WithProperties('%s', '%s-revision' % repoName),
                          '-m',
                          # This part is pretty ugly. Because we need both
                          # WithProperties interpolation (for repoName-revision)
                          # and regular variables we need to piece it together
                          # this way.
                          WithProperties('Added tag ' + tag + \
                            ' for changeset ' + \
                            '%(' + repoName + '-revision' + ')s. ' + \
                            'CLOSED TREE'),
                          tag],
                 workdir=repoName,
                 description=['tag %s' % repoName],
                 haltOnFailure=True
                )
            self.addStep(ShellCommand,
             name='hg_out',
             command=['hg', 'out', '-e',
                      'ssh -l %s %s' % (hgUsername, sshKeyOption),
                      pushRepo],
             workdir=repoName,
             description=['hg out', repoName]
            )
            self.addStep(ShellCommand,
             name='hg_push',
             command=['hg', 'push', '-e',
                      'ssh -l %s %s' % (hgUsername, sshKeyOption),
                      '-f', pushRepo],
             workdir=repoName,
             description=['push %s' % repoName],
             haltOnFailure=True
            )



class SingleSourceFactory(ReleaseFactory):
    def __init__(self, productName, version, baseTag, stagingServer,
                 stageUsername, stageSshKey, buildNumber, autoconfDirs=['.'],
                 buildSpace=1, **kwargs):
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)
        releaseTag = '%s_RELEASE' % (baseTag)
        bundleFile = 'source/%s-%s.bundle' % (productName, version)
        sourceTarball = 'source/%s-%s.source.tar.bz2' % (productName,
                                                         version)
        # '-c' is for "release to candidates dir"
        postUploadCmd = 'post_upload.py -p %s -v %s -n %s -c' % \
          (productName, version, buildNumber)
        uploadEnv = {'UPLOAD_HOST': stagingServer,
                     'UPLOAD_USER': stageUsername,
                     'UPLOAD_SSH_KEY': '~/.ssh/%s' % stageSshKey,
                     'UPLOAD_TO_TEMP': '1',
                     'POST_UPLOAD_CMD': postUploadCmd}

        self.addStep(ShellCommand,
         name='rm_srcdir',
         command=['rm', '-rf', 'source'],
         workdir='.',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='make_srcdir',
         command=['mkdir', 'source'],
         workdir='.',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='hg_clone',
         command=['hg', 'clone', self.repository, self.branchName],
         workdir='.',
         description=['clone %s' % self.branchName],
         haltOnFailure=True,
         timeout=30*60 # 30 minutes
        )
        # This will get us to the version we're building the release with
        self.addStep(ShellCommand,
         name='hg_update',
         command=['hg', 'up', '-C', '-r', releaseTag],
         workdir=self.branchName,
         description=['update to', releaseTag],
         haltOnFailure=True
        )
        # ...And this will get us the tags so people can do things like
        # 'hg up -r FIREFOX_3_1b1_RELEASE' with the bundle
        self.addStep(ShellCommand,
         name='hg_update_incl_tags',
         command=['hg', 'up'],
         workdir=self.branchName,
         description=['update to', 'include tag revs'],
         haltOnFailure=True
        )
        self.addStep(SetProperty,
         name='hg_ident_revision',
         command=['hg', 'identify', '-i'],
         property='revision',
         workdir=self.branchName,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='create_bundle',
         command=['hg', '-R', self.branchName, 'bundle', '--base', 'null',
                  '-r', WithProperties('%(revision)s'),
                  bundleFile],
         workdir='.',
         description=['create bundle'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='delete_metadata',
         command=['rm', '-rf', '.hg'],
         workdir=self.branchName,
         description=['delete metadata'],
         haltOnFailure=True
        )
        for dir in autoconfDirs:
            self.addStep(ShellCommand,
             name='autoconf',
             command=['autoconf-2.13'],
             workdir='%s/%s' % (self.branchName, dir),
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         name='create_tarball',
         command=['tar', '-cjf', sourceTarball, self.branchName],
         workdir='.',
         description=['create tarball'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='upload_files',
         command=['python', '%s/build/upload.py' % self.branchName,
                  '--base-path', '.',
                  bundleFile, sourceTarball],
         workdir='.',
         env=uploadEnv,
         description=['upload files'],
        )

class MultiSourceFactory(ReleaseFactory):
    """You need to pass in a repoConfig, which will be a list that
       looks like this:
       repoConfig = [{
           'repoPath': repoPath,
           'location': branchName,
           'bundleName': '%s-%s.bundle' % (productName, version)
       }]"""
    def __init__(self, productName, version, baseTag, stagingServer,
                 stageUsername, stageSshKey, buildNumber, autoconfDirs=['.'],
                 buildSpace=1, repoConfig=None, uploadProductName=None,
                 stageNightlyDir="nightly", **kwargs):
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)
        releaseTag = '%s_RELEASE' % (baseTag)
        bundleFiles = []
        sourceTarball = 'source/%s-%s.source.tar.bz2' % (productName,
                                                         version)
        if not uploadProductName:
            uploadProductName = productName

        assert repoConfig
        # '-c' is for "release to candidates dir"
        postUploadCmd = 'post_upload.py -p %s -v %s -n %s -c --nightly-dir %s' % \
          (uploadProductName, version, buildNumber, stageNightlyDir)
        uploadEnv = {'UPLOAD_HOST': stagingServer,
                     'UPLOAD_USER': stageUsername,
                     'UPLOAD_SSH_KEY': '~/.ssh/%s' % stageSshKey,
                     'UPLOAD_TO_TEMP': '1',
                     'POST_UPLOAD_CMD': postUploadCmd}

        self.addStep(ShellCommand,
         name='rm_srcdir',
         command=['rm', '-rf', 'source'],
         workdir='.',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='make_srcdir',
         command=['mkdir', 'source'],
         workdir='.',
         haltOnFailure=True
        )
        for repo in repoConfig:
            repository = self.getRepository(repo['repoPath'])
            location = repo['location']
            bundleFiles.append('source/%s' % repo['bundleName'])

            self.addStep(ShellCommand,
             name='hg_clone',
             command=['hg', 'clone', repository, location],
             workdir='.',
             description=['clone %s' % location],
             haltOnFailure=True,
             timeout=30*60 # 30 minutes
            )
            # This will get us to the version we're building the release with
            self.addStep(ShellCommand,
             name='hg_update',
             command=['hg', 'up', '-C', '-r', releaseTag],
             workdir=location,
             description=['update to', releaseTag],
             haltOnFailure=True
            )
            # ...And this will get us the tags so people can do things like
            # 'hg up -r FIREFOX_3_1b1_RELEASE' with the bundle
            self.addStep(ShellCommand,
             name='hg_update_incl_tags',
             command=['hg', 'up'],
             workdir=location,
             description=['update to', 'include tag revs'],
             haltOnFailure=True
            )
            self.addStep(SetProperty,
             name='hg_ident_revision',
             command=['hg', 'identify', '-i'],
             property='revision',
             workdir=location,
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             name='create_bundle',
             command=['hg', '-R', location, 'bundle', '--base', 'null',
                      '-r', WithProperties('%(revision)s'),
                      'source/%s' % repo['bundleName']],
             workdir='.',
             description=['create bundle'],
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             name='delete_metadata',
             command=['rm', '-rf', '.hg'],
             workdir=location,
             description=['delete metadata'],
             haltOnFailure=True
            )
        for dir in autoconfDirs:
            self.addStep(ShellCommand,
             name='autoconf',
             command=['autoconf-2.13'],
             workdir='%s/%s' % (self.branchName, dir),
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         name='create_tarball',
         command=['tar', '-cjf', sourceTarball, self.branchName],
         workdir='.',
         description=['create tarball'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='upload_files',
         command=['python', '%s/build/upload.py' % self.branchName,
                  '--base-path', '.'] + bundleFiles + [sourceTarball],
         workdir='.',
         env=uploadEnv,
         description=['upload files'],
        )

class CCSourceFactory(ReleaseFactory):
    def __init__(self, productName, version, baseTag, stagingServer,
                 stageUsername, stageSshKey, buildNumber, mozRepoPath,
                 inspectorRepoPath='', venkmanRepoPath='',
                 chatzillaRepoPath='', cvsroot='', autoconfDirs=['.'],
                 buildSpace=1, **kwargs):
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)
        releaseTag = '%s_RELEASE' % (baseTag)
        sourceTarball = 'source/%s-%s.source.tar.bz2' % (productName,
                                                         version)
        # '-c' is for "release to candidates dir"
        postUploadCmd = 'post_upload.py -p %s -v %s -n %s -c' % \
          (productName, version, buildNumber)
        uploadEnv = {'UPLOAD_HOST': stagingServer,
                     'UPLOAD_USER': stageUsername,
                     'UPLOAD_SSH_KEY': '~/.ssh/%s' % stageSshKey,
                     'UPLOAD_TO_TEMP': '1',
                     'POST_UPLOAD_CMD': postUploadCmd}

        self.addStep(ShellCommand,
         command=['rm', '-rf', 'source'],
         workdir='.',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['mkdir', 'source'],
         workdir='.',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['hg', 'clone', self.repository, self.branchName],
         workdir='.',
         description=['clone %s' % self.branchName],
         haltOnFailure=True,
         timeout=30*60 # 30 minutes
        )
        # build up the checkout command that will bring us up to the release version
        co_command = ['python', 'client.py', 'checkout',
                      '--comm-rev=%s' % releaseTag,
                      '--mozilla-repo=%s' % self.getRepository(mozRepoPath),
                      '--mozilla-rev=%s' % releaseTag]
        if inspectorRepoPath:
            co_command.append('--inspector-repo=%s' % self.getRepository(inspectorRepoPath))
            co_command.append('--inspector-rev=%s' % releaseTag)
        else:
            co_command.append('--skip-inspector')
        if venkmanRepoPath:
            co_command.append('--venkman-repo=%s' % self.getRepository(venkmanRepoPath))
            co_command.append('--venkman-rev=%s' % releaseTag)
        else:
            co_command.append('--skip-venkman')
        if chatzillaRepoPath:
            co_command.append('--chatzilla-repo=%s' % self.getRepository(chatzillaRepoPath))
            co_command.append('--chatzilla-rev=%s' % releaseTag)
        else:
            co_command.append('--skip-chatzilla')
        if cvsroot:
            co_command.append('--cvsroot=%s' % cvsroot)
        # execute the checkout
        self.addStep(ShellCommand,
         command=co_command,
         workdir=self.branchName,
         description=['update to', releaseTag],
         haltOnFailure=True,
         timeout=60*60 # 1 hour
        )
        # the autoconf and actual tarring steps
        # should be replaced by calling the build target
        for dir in autoconfDirs:
            self.addStep(ShellCommand,
             command=['autoconf-2.13'],
             workdir='%s/%s' % (self.branchName, dir),
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         command=['tar', '-cj', '--owner=0', '--group=0', '--numeric-owner',
                  '--mode=go-w', '--exclude=.hg*', '--exclude=CVS',
                  '--exclude=.cvs*', '-f', sourceTarball, self.branchName],
         workdir='.',
         description=['create tarball'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['python', '%s/mozilla/build/upload.py' % self.branchName,
                  '--base-path', '.', sourceTarball],
         workdir='.',
         env=uploadEnv,
         description=['upload files'],
        )



class ReleaseUpdatesFactory(ReleaseFactory):
    def __init__(self, cvsroot, patcherToolsTag, patcherConfig, verifyConfigs,
                 appName, productName,
                 version, appVersion, baseTag, buildNumber,
                 oldVersion, oldAppVersion, oldBaseTag,  oldBuildNumber,
                 ftpServer, bouncerServer, stagingServer, useBetaChannel,
                 stageUsername, stageSshKey, ausUser, ausHost, ausServerUrl,
                 hgSshKey, hgUsername, commitPatcherConfig=True, mozRepoPath=None,
                 brandName=None, buildSpace=14, **kwargs):
        """cvsroot: The CVSROOT to use when pulling patcher, patcher-configs,
                    Bootstrap/Util.pm, and MozBuild. It is also used when
                    commiting the version-bumped patcher config so it must have
                    write permission to the repository if commitPatcherConfig
                    is True.
           patcherToolsTag: A tag that has been applied to all of:
                              sourceRepo, patcher, MozBuild, Bootstrap.
                            This version of all of the above tools will be
                            used - NOT tip.
           patcherConfig: The filename of the patcher config file to bump,
                          and pass to patcher.
           commitPatcherConfig: This flag simply controls whether or not
                                the bumped patcher config file will be
                                commited to the CVS repository.
           mozRepoPath: The path for the Mozilla repo to hand patcher as the
                        HGROOT (if omitted, the default repoPath is used).
                        Apps not rooted in the Mozilla repo need this.
           brandName: The brand name as used on the updates server. If omitted,
                      the first letter of the brand name is uppercased.
        """
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)

        patcherConfigFile = 'patcher-configs/%s' % patcherConfig
        shippedLocales = self.getShippedLocales(self.repository, baseTag,
                                                appName)
        candidatesDir = self.getCandidatesDir(productName, version, buildNumber)
        updateDir = 'build/temp/%s/%s-%s' % (productName, oldVersion, version)
        marDir = '%s/ftp/%s/nightly/%s-candidates/build%s' % \
          (updateDir, productName, version, buildNumber)

        if mozRepoPath:
          self.mozRepository = self.getRepository(mozRepoPath)
        else:
          self.mozRepository = self.repository

        if not brandName:
            brandName = productName.capitalize()

        # If useBetaChannel is False the unnamed snippet type will be
        # 'beta' channel snippets (and 'release' if we're into stable releases).
        # If useBetaChannel is True the unnamed type will be 'release'
        # channel snippets
        snippetTypes = ['', 'test']
        if useBetaChannel:
            snippetTypes.append('beta')

        # General setup
        self.addStep(ShellCommand,
         name='checkout_patcher',
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d', 'build', 'mozilla/tools/patcher'],
         description=['checkout', 'patcher'],
         workdir='.',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='checkout_mozbuild',
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d', 'MozBuild',
                  'mozilla/tools/release/MozBuild'],
         description=['checkout', 'MozBuild'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='checkout_bootstrap_util',
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d' 'Bootstrap',
                  'mozilla/tools/release/Bootstrap/Util.pm'],
         description=['checkout', 'Bootstrap/Util.pm'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='checkout_patcher_configs',
         command=['cvs', '-d', cvsroot, 'co', '-d' 'patcher-configs',
                  'mozilla/tools/patcher-configs'],
         description=['checkout', 'patcher-configs'],
         haltOnFailure=True
        )

        # Bump the patcher config
        self.addStep(ShellCommand,
         name='get_shipped_locales',
         command=['wget', '-O', 'shipped-locales', shippedLocales],
         description=['get', 'shipped-locales'],
         haltOnFailure=True
        )

        bumpCommand = ['perl', '../tools/release/patcher-config-bump.pl',
                       '-p', productName, '-r', brandName, '-v', version,
                       '-a', appVersion, '-o', oldVersion, '-b', str(buildNumber),
                       '-c', patcherConfigFile, '-t', stagingServer,
                       '-f', ftpServer, '-d', bouncerServer,
                       '-l', 'shipped-locales']
        if useBetaChannel:
            bumpCommand.append('-u')
        self.addStep(ShellCommand,
         name='bump',
         command=bumpCommand,
         description=['bump', patcherConfig],
         haltOnFailure=True
        )
        self.addStep(TinderboxShellCommand,
         name='diff_patcher_config',
         command=['cvs', 'diff', '-u', patcherConfigFile],
         description=['diff', patcherConfig],
         ignoreCodes=[1]
        )
        if commitPatcherConfig:
            self.addStep(ShellCommand,
             name='commit_patcher_config',
             command=['cvs', 'commit', '-m',
                      'Automated configuration bump: ' + \
                      '%s, from %s to %s build %s' % \
                        (patcherConfig, oldVersion, version, buildNumber)
                     ],
             workdir='build/patcher-configs',
             description=['commit', patcherConfig],
             haltOnFailure=True
            )

        # Bump the update verify config
        oldLongVersion = self.makeLongVersion(oldVersion)
        longVersion = self.makeLongVersion(version)
        oldCandidatesDir = self.getCandidatesDir(productName, oldVersion,
                                                 oldBuildNumber)

        oldShippedLocales = self.getShippedLocales(self.repository, oldBaseTag,
                                                appName)
        pushRepo = self.getRepository(self.buildToolsRepoPath, push=True)
        sshKeyOption = self.getSshKeyOption(hgSshKey)

        self.addStep(ShellCommand,
         name='get_old_shipped_locales',
         command=['wget', '-O', 'old-shipped-locales', oldShippedLocales],
         description=['get', 'old-shipped-locales'],
         haltOnFailure=True
        )
        for platform in sorted(verifyConfigs.keys()):
            verifyConfigPath = '../tools/release/updates/%s' % \
                                verifyConfigs[platform]
            self.addStep(ShellCommand,
             name='bump_verify_configs',
             command=['perl', '../tools/release/update-verify-bump.pl',
                      '-o', platform, '-p', productName, '-r', brandName,
                      '--old-version=%s' % oldVersion,
                      '--old-app-version=%s' % oldAppVersion,
                      '--old-long-version=%s' % oldLongVersion,
                      '-v', version, '--app-version=%s' % appVersion,
                      '--long-version=%s' % longVersion,
                      '-n', str(buildNumber), '-a', ausServerUrl,
                      '-s', stagingServer, '-c', verifyConfigPath,
                      '-d', oldCandidatesDir, '-l', 'old-shipped-locales',
                      '--pretty-candidates-dir'],
             description=['bump', verifyConfigs[platform]],
            )
        self.addStep(ShellCommand,
         name='commit_verify_configs',
         command=['hg', 'commit', '-m',
                  'Automated configuration bump: update verify configs ' + \
                  'for %s build %s' % (version, buildNumber)],
         description=['commit verify configs'],
         workdir='tools',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='push_verify_configs',
         command=['hg', 'push', '-e',
                  'ssh -l %s %s' % (hgUsername, sshKeyOption),
                  '-f', pushRepo],
         description=['push verify configs'],
         workdir='tools',
         haltOnFailure=True
        )

        # Generate updates from here
        self.addStep(ShellCommand,
         name='patcher_build_tools',
         command=['perl', 'patcher2.pl', '--build-tools-hg',
                  '--tools-revision=%s' % patcherToolsTag,
                  '--app=%s' % productName,
                  '--brand=%s' % brandName,
                  '--config=%s' % patcherConfigFile],
         description=['patcher:', 'build tools'],
         env={'HGROOT': self.mozRepository},
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='patcher_download_builds',
         command=['perl', 'patcher2.pl', '--download',
                  '--app=%s' % productName,
                  '--brand=%s' % brandName,
                  '--config=%s' % patcherConfigFile],
         description=['patcher:', 'download builds'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='patcher_create_patches',
         command=['perl', 'patcher2.pl', '--create-patches',
                  '--partial-patchlist-file=patchlist.cfg',
                  '--app=%s' % productName,
                  '--brand=%s' % brandName,
                  '--config=%s' % patcherConfigFile],
         description=['patcher:', 'create patches'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='chmod_partial_mars',
         command=['find', marDir, '-type', 'f',
                  '-exec', 'chmod', '644', '{}', ';'],
         workdir='.',
         description=['chmod 644', 'partial mar files']
        )
        self.addStep(ShellCommand,
         name='chmod_partial_mar_dirs',
         command=['find', marDir, '-type', 'd',
                  '-exec', 'chmod', '755', '{}', ';'],
         workdir='.',
         description=['chmod 755', 'partial mar dirs']
        )
        self.addStep(ShellCommand,
         name='upload_partial_mars',
         command=['rsync', '-av',
                  '-e', 'ssh -oIdentityFile=~/.ssh/%s' % stageSshKey,
                  '--exclude=*complete.mar',
                  'update',
                  '%s@%s:%s' % (stageUsername, stagingServer, candidatesDir)],
         workdir=marDir,
         description=['upload', 'partial mars'],
         haltOnFailure=True
        )
        # It gets a little hairy down here
        date = strftime('%Y%m%d')
        for type in snippetTypes:
            # Patcher generates an 'aus2' directory and 'aus2.snippetType'
            # directories for each snippetType. Typically, there is 'aus2',
            # 'aus2.test', and (when we're out of beta) 'aus2.beta'.
            localDir = 'aus2'
            # On the AUS server we store each type of snippet in a directory
            # named thusly, with the snippet type appended to it
            remoteDir = '%s-%s-%s' % (date, brandName, version)
            if type != '':
                localDir = localDir + '.%s' % type
                remoteDir = remoteDir + '-%s' % type
            snippetDir = '/opt/aus2/snippets/staging/%s' % remoteDir

            self.addStep(ShellCommand,
             name='upload_snipets',
             command=['rsync', '-av', localDir + '/',
                      '%s@%s:%s' % (ausUser, ausHost, snippetDir)],
             workdir=updateDir,
             description=['upload', '%s snippets' % type],
             haltOnFailure=True
            )

            # We only push test channel snippets from automation.
            if type == 'test':
                self.addStep(ShellCommand,
                 name='backupsnip',
                 command=['ssh', '-l', ausUser, ausHost,
                          '~/bin/backupsnip %s' % remoteDir],
                 timeout=7200, # 2 hours
                 description=['backupsnip'],
                 haltOnFailure=True
                )
                self.addStep(ShellCommand,
                 name='pushsnip',
                 command=['ssh', '-l', ausUser, ausHost,
                          '~/bin/pushsnip %s' % remoteDir],
                 timeout=3600, # 1 hour
                 description=['pushsnip'],
                 haltOnFailure=True
                )
                # Wait for timeout on AUS's NFS caching to expire before
                # attempting to test newly-pushed snippets
                self.addStep(ShellCommand,
                 name='wait_live_snippets',
                 command=['sleep','360'],
                 description=['wait for live snippets']
                )



class UpdateVerifyFactory(ReleaseFactory):
    def __init__(self, verifyConfig, buildSpace=.3, **kwargs):
        ReleaseFactory.__init__(self, repoPath='nothing',
                                buildSpace=buildSpace, **kwargs)
        self.addStep(UpdateVerify,
         command=['bash', 'verify.sh', '-c', verifyConfig],
         workdir='tools/release/updates',
         description=['./verify.sh', verifyConfig]
        )

class ReleaseFinalVerification(ReleaseFactory):
    def __init__(self, verifyConfigs, **kwargs):
        # MozillaBuildFactory needs the 'repoPath' argument, but we don't
        ReleaseFactory.__init__(self, repoPath='nothing', **kwargs)
        verifyCommand = ['bash', 'final-verification.sh']
        for platform in sorted(verifyConfigs.keys()):
            verifyCommand.append(verifyConfigs[platform])
        self.addStep(ShellCommand,
         name='final_verification',
         command=verifyCommand,
         description=['final-verification.sh'],
         workdir='tools/release'
        )

class UnittestBuildFactory(MozillaBuildFactory):
    def __init__(self, platform, productName, config_repo_path, config_dir,
            objdir, mochitest_leak_threshold=None,
            crashtest_leak_threshold=None, uploadPackages=False,
            unittestMasters=None, unittestBranch=None, stageUsername=None,
            stageServer=None, stageSshKey=None, run_a11y=True,
            env={}, **kwargs):
        self.env = {}

        MozillaBuildFactory.__init__(self, **kwargs)

        self.productName = productName
        self.stageServer = stageServer
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey
        self.uploadPackages = uploadPackages
        self.config_repo_path = config_repo_path
        self.config_dir = config_dir
        self.objdir = objdir
        self.run_a11y = run_a11y
        self.crashtest_leak_threshold = crashtest_leak_threshold
        self.mochitest_leak_threshold = mochitest_leak_threshold

        self.unittestMasters = unittestMasters or []
        self.unittestBranch = unittestBranch
        if self.unittestMasters:
            assert self.unittestBranch

        self.config_repo_url = self.getRepository(self.config_repo_path)

        env_map = {
                'linux': 'linux-unittest',
                'macosx': 'macosx-unittest',
                'win32': 'win32-unittest',
                }

        self.platform = platform.split('-')[0]
        assert self.platform in ('linux', 'linux64', 'win32', 'macosx')

        self.env = MozillaEnvironments[env_map[self.platform]].copy()
        self.env['MOZ_OBJDIR'] = self.objdir
        self.env.update(env)

        if self.platform == 'win32':
            self.addStep(TinderboxShellCommand,
             name='kill_sh',
             description='kill sh',
             descriptionDone="killed sh",
             command="pskill -t sh.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand,
             name='kill_make',
             description='kill make',
             descriptionDone="killed make",
             command="pskill -t make.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand,
             name='kill_firefox',
             description='kill firefox',
             descriptionDone="killed firefox",
             command="pskill -t firefox.exe",
             workdir="D:\\Utilities"
            )

        self.addStepNoEnv(Mercurial, 
         name='hg_update',
         mode='update',
         baseURL='http://%s/' % self.hgHost,
         defaultBranch=self.repoPath,
         timeout=60*60 # 1 hour
        )

        self.addPrintChangesetStep()

        self.addStep(ShellCommand,
         name='rm_configs',
         command=['rm', '-rf', 'mozconfigs'],
         workdir='.'
        )

        self.addStep(ShellCommand,
         name='buildbot_configs',
         command=['hg', 'clone', self.config_repo_url, 'mozconfigs'],
         workdir='.'
        )

        self.addCopyMozconfigStep()

        # TODO: Do we need this special windows rule?
        if self.platform == 'win32':
            self.addStep(ShellCommand,
             name='mozconfig_contents',
             command=["type", ".mozconfig"]
            )
        else:
            self.addStep(ShellCommand,
             name='mozconfig_contents',
             command=['cat', '.mozconfig']
            )

        self.addStep(ShellCommand,
         name='compile',
         command=["make", "-f", "client.mk", "build"],
         description=['compile'],
         timeout=60*60, # 1 hour
         haltOnFailure=1
        )

        self.addStep(ShellCommand,
         name='make_buildsymbols',
         command=['make', 'buildsymbols'],
         workdir='build/%s' % self.objdir,
         timeout=60*60
        )

        if self.uploadPackages:
            self.addStep(ShellCommand,
             name='make_pkg',
             command=['make', 'package'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             name='make_pkg_tests',
             command=['make', 'package-tests'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True
            )
            self.addStep(GetBuildID,
             name='get_build_id',
             objdir=self.objdir,
            )

            uploadEnv = self.env.copy()
            uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                              'UPLOAD_USER': self.stageUsername,
                              'UPLOAD_TO_TEMP': '1'})
            if self.stageSshKey:
                uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

            # Always upload builds to the dated tinderbox builds directories
            postUploadCmd =  ['post_upload.py']
            postUploadCmd += ['--tinderbox-builds-dir %s-%s-unittest' %
                                    (self.branchName, self.platform),
                              '-i %(buildid)s',
                              '-p %s' % self.productName,
                              '--release-to-tinderbox-dated-builds']

            uploadEnv['POST_UPLOAD_CMD'] = WithProperties(' '.join(postUploadCmd))
            def get_url(rc, stdout, stderr):
                m = re.search("^(http://.*?\.(tar\.bz2|dmg|zip))", "\n".join([stdout, stderr]), re.M)
                if m:
                    return {'packageUrl': m.group(1)}
                return {}
            self.addStep(SetProperty,
             command=['make', 'upload'],
             env=uploadEnv,
             workdir='build/%s' % self.objdir,
             extract_fn = get_url,
             haltOnFailure=True,
             description=['upload']
            )

            for master, warn, retries in self.unittestMasters:
                self.addStep(SendChangeStep(
                 name='sendchange_%s' % master,
                 warnOnFailure=warn,
                 master=master,
                 retries=retries,
                 branch=self.unittestBranch,
                 files=[WithProperties('%(packageUrl)s')],
                 user="sendchange-unittest")
                )

        self.addStep(SetProperty,
         command=['bash', '-c', 'pwd'],
         property='toolsdir',
         workdir='tools'
        )

        self.env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)

        self.addPreTestSteps()

        self.addTestSteps()

        self.addPostTestSteps()

        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def addTestSteps(self):
        self.addStep(unittest_steps.MozillaCheck,
         test_name="check",
         warnOnWarnings=True,
         workdir="build/%s" % self.objdir,
         timeout=5*60, # 5 minutes.
         env=self.env,
        )

    def addPrintChangesetStep(self):
        changesetLink = ''.join(['<a href=http://hg.mozilla.org/',
            self.repoPath,
            '/rev/%(got_revision)s title="Built from revision %(got_revision)s">rev:%(got_revision)s</a>'])
        self.addStep(OutputStep(
         name='tinderboxprint_changeset',
         data=['TinderboxPrint:', WithProperties(changesetLink)],
        ))

    def addStep(self, *args, **kw):
        kw.setdefault('env', self.env)
        return BuildFactory.addStep(self, *args, **kw)

    def addStepNoEnv(self, *args, **kw):
        return BuildFactory.addStep(self, *args, **kw)

    def addCopyMozconfigStep(self):
        config_dir_map = {
                'linux': 'linux/%s/unittest' % self.branchName,
                'macosx': 'macosx/%s/unittest' % self.branchName,
                'win32': 'win32/%s/unittest' % self.branchName,
                }
        mozconfig = 'mozconfigs/%s/%s/mozconfig' % \
            (self.config_dir, config_dir_map[self.platform])

        self.addStep(ShellCommand,
         name='copy_mozconfig',
         command=['cp', mozconfig, 'build/.mozconfig'],
         description=['copy mozconfig'],
         workdir='.'
        )

    def addPreTestSteps(self):
        pass

    def addPostTestSteps(self):
        pass

class CCUnittestBuildFactory(MozillaBuildFactory):
    def __init__(self, platform, productName, config_repo_path, config_dir,
            objdir, mozRepoPath, brandName=None, mochitest_leak_threshold=None,
            mochichrome_leak_threshold=None, mochibrowser_leak_threshold=None,
            crashtest_leak_threshold=None, uploadPackages=False,
            unittestMasters=None, stageUsername=None, stageServer=None,
            stageSshKey=None, exec_reftest_suites=True, exec_mochi_suites=True,
            exec_mozmill_suites=False, run_a11y=True, **kwargs):
        self.env = {}

        MozillaBuildFactory.__init__(self, **kwargs)

        self.productName = productName
        self.stageServer = stageServer
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey
        self.uploadPackages = uploadPackages
        self.config_repo_path = config_repo_path
        self.mozRepoPath = mozRepoPath
        self.config_dir = config_dir
        self.objdir = objdir
        self.run_a11y = run_a11y
        if unittestMasters is None:
            self.unittestMasters = []
        else:
            self.unittestMasters = unittestMasters
        if brandName:
            self.brandName = brandName
        else:
            self.brandName = productName.capitalize()
        self.mochitest_leak_threshold = mochitest_leak_threshold
        self.mochichrome_leak_threshold = mochichrome_leak_threshold
        self.mochibrowser_leak_threshold = mochibrowser_leak_threshold
        self.exec_reftest_suites = exec_reftest_suites
        self.exec_mochi_suites = exec_mochi_suites
        self.exec_mozmill_suites = exec_mozmill_suites

        self.config_repo_url = self.getRepository(self.config_repo_path)

        env_map = {
                'linux': 'linux-unittest',
                'macosx': 'macosx-unittest',
                'win32': 'win32-unittest',
                }

        self.platform = platform.split('-')[0]
        assert self.platform in ('linux', 'linux64', 'win32', 'macosx')

        # Mozilla subdir and objdir
        self.mozillaDir = '/mozilla'
        self.mozillaObjdir = '%s%s' % (self.objdir, self.mozillaDir)

        self.env = MozillaEnvironments[env_map[self.platform]].copy()
        self.env['MOZ_OBJDIR'] = self.objdir

        if self.platform == 'win32':
            self.addStep(TinderboxShellCommand,
             name='kill_hg',
             description='kill hg',
             descriptionDone="killed hg",
             command="pskill -t hg.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand,
             name='kill_sh',
             description='kill sh',
             descriptionDone="killed sh",
             command="pskill -t sh.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand,
             name='kill_make',
             description='kill make',
             descriptionDone="killed make",
             command="pskill -t make.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand,
             name="kill_%s" % self.productName,
             description='kill %s' % self.productName,
             descriptionDone="killed %s" % self.productName,
             command="pskill -t %s.exe" % self.productName,
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand, name="kill xpcshell",
             description='kill_xpcshell',
             descriptionDone="killed xpcshell",
             command="pskill -t xpcshell.exe",
             workdir="D:\\Utilities"
            )

        self.addStepNoEnv(Mercurial, mode='update',
         baseURL='http://%s/' % self.hgHost,
         defaultBranch=self.repoPath,
         alwaysUseLatest=True,
         timeout=60*60 # 1 hour
        )

        self.addPrintChangesetStep()

        self.addStepNoEnv(ShellCommand,
         name='checkout_client.py',
         command=['python', 'client.py', 'checkout',
                  '--mozilla-repo=%s' % self.getRepository(self.mozRepoPath)],
         description=['running', 'client.py', 'checkout'],
         descriptionDone=['client.py', 'checkout'],
         haltOnFailure=True,
         timeout=60*60 # 1 hour
        )

        self.addPrintMozillaChangesetStep()

        self.addStep(ShellCommand,
         name='rm_configs',
         command=['rm', '-rf', 'mozconfigs'],
         workdir='.'
        )

        self.addStep(ShellCommand,
         name='buildbot_configs',
         command=['hg', 'clone', self.config_repo_url, 'mozconfigs'],
         workdir='.'
        )

        self.addCopyMozconfigStep()

        self.addStep(ShellCommand,
         name='mozconfig_contents',
         command=['cat', '.mozconfig']
        )

        self.addStep(ShellCommand,
         name='compile',
         command=["make", "-f", "client.mk", "build"],
         description=['compile'],
         timeout=60*60, # 1 hour
         haltOnFailure=1
        )

        self.addStep(ShellCommand,
         name='make_buildsymbols',
         command=['make', 'buildsymbols'],
         workdir='build/%s' % self.objdir,
         )

        if self.uploadPackages:
            self.addStep(ShellCommand,
             name='make_pkg',
             command=['make', 'package'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             name='make_pkg_tests',
             command=['make', 'package-tests'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True
            )
            if self.mozillaDir == '':
                getpropsObjdir = self.objdir
            else:
                getpropsObjdir = '../%s' % self.mozillaObjdir
            self.addStep(GetBuildID,
             objdir=getpropsObjdir,
             workdir='build%s' % self.mozillaDir,
            )

            uploadEnv = self.env.copy()
            uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                              'UPLOAD_USER': self.stageUsername,
                              'UPLOAD_TO_TEMP': '1'})
            if self.stageSshKey:
                uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

            # Always upload builds to the dated tinderbox builds directories
            postUploadCmd =  ['post_upload.py']
            postUploadCmd += ['--tinderbox-builds-dir %s-%s-unittest' %
                                    (self.branchName, self.platform),
                              '-i %(buildid)s',
                              '-p %s' % self.productName,
                              '--release-to-tinderbox-dated-builds']

            uploadEnv['POST_UPLOAD_CMD'] = WithProperties(' '.join(postUploadCmd))
            def get_url(rc, stdout, stderr):
                m = re.search("^(http://.*?\.(tar\.bz2|dmg|zip))", "\n".join([stdout, stderr]), re.M)
                if m:
                    return {'packageUrl': m.group(1)}
                return {}
            self.addStep(SetProperty,
             command=['make', 'upload'],
             env=uploadEnv,
             workdir='build/%s' % self.objdir,
             extract_fn = get_url,
             haltOnFailure=True,
             description=['upload']
            )

            branch = "%s-%s-unittest" % (self.branchName, self.platform)
            for master, warn in self.unittestMasters:
                self.addStep(SendChangeStep(
                 warnOnFailure=warn,
                 master=master,
                 branch=branch,
                 files=[WithProperties('%(packageUrl)s')],
                 user="sendchange-unittest")
                )

        self.addStep(SetProperty,
         command=['bash', '-c', 'pwd'],
         property='toolsdir',
         workdir='tools'
        )

        self.env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)

        self.addPreTestSteps()

        self.addStep(unittest_steps.MozillaCheck,
         test_name="check",
         warnOnWarnings=True,
         workdir="build/%s" % self.objdir,
         timeout=5*60, # 5 minutes.
        )

        self.addStep(unittest_steps.MozillaCheck,
         test_name="xpcshell-tests",
         warnOnWarnings=True,
         workdir="build/%s" % self.objdir,
         timeout=5*60, # 5 minutes.
        )

        if self.exec_mozmill_suites:
            mozmillEnv = self.env.copy()
            mozmillEnv['NO_EM_RESTART'] = "0"
            self.addStep(unittest_steps.MozillaCheck,
             test_name="mozmill",
             warnOnWarnings=True,
             workdir="build/%s" % self.objdir,
             timeout=5*60, # 5 minutes.
             env=mozmillEnv,
            )

        if self.exec_reftest_suites:
            self.addStep(unittest_steps.MozillaReftest, warnOnWarnings=True,
             test_name="reftest",
             workdir="build/%s" % self.objdir,
             timeout=5*60,
            )
            self.addStep(unittest_steps.MozillaReftest, warnOnWarnings=True,
             test_name="crashtest",
             leakThreshold=crashtest_leak_threshold,
             workdir="build/%s" % self.objdir,
            )

        if self.exec_mochi_suites:
            self.addStep(unittest_steps.MozillaMochitest, warnOnWarnings=True,
             test_name="mochitest-plain",
             workdir="build/%s" % self.objdir,
             leakThreshold=self.mochitest_leak_threshold,
             timeout=5*60,
            )
            self.addStep(unittest_steps.MozillaMochitest, warnOnWarnings=True,
             test_name="mochitest-chrome",
             workdir="build/%s" % self.objdir,
             leakThreshold=self.mochichrome_leak_threshold,
            )
            self.addStep(unittest_steps.MozillaMochitest, warnOnWarnings=True,
             test_name="mochitest-browser-chrome",
             workdir="build/%s" % self.objdir,
             leakThreshold=self.mochibrowser_leak_threshold,
            )
            if self.run_a11y:
                self.addStep(unittest_steps.MozillaMochitest, warnOnWarnings=True,
                 test_name="mochitest-a11y",
                 workdir="build/%s" % self.objdir,
                )

        self.addPostTestSteps()

        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def addPrintChangesetStep(self):
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost, self.repoPath)
        changesetLink += '/%(got_revision)s title="Built from revision %(got_revision)s">rev:%(got_revision)s</a>'
        self.addStep(OutputStep(
         name='tinderboxprint_changeset',
         data=['TinderboxPrint:', WithProperties(changesetLink)],
        ))

    def addPrintMozillaChangesetStep(self):
        self.addStep(SetProperty,
         command=['hg', 'identify', '-i'],
         workdir='build%s' % self.mozillaDir,
         property='hg_revision'
        )
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost, self.mozRepoPath)
        changesetLink += '/%(hg_revision)s title="Built from Mozilla revision %(hg_revision)s">moz:%(hg_revision)s</a>'
        self.addStep(OutputStep(
         name='tinderboxprint_changeset',
         data=['TinderboxPrint:', WithProperties(changesetLink)]
        ))

    def addStep(self, *args, **kw):
        kw.setdefault('env', self.env)
        return BuildFactory.addStep(self, *args, **kw)

    def addStepNoEnv(self, *args, **kw):
        return BuildFactory.addStep(self, *args, **kw)

    def addCopyMozconfigStep(self):
        config_dir_map = {
                'linux': 'linux/%s/unittest' % self.branchName,
                'macosx': 'macosx/%s/unittest' % self.branchName,
                'win32': 'win32/%s/unittest' % self.branchName,
                }
        mozconfig = 'mozconfigs/%s/%s/mozconfig' % \
            (self.config_dir, config_dir_map[self.platform])

        self.addStep(ShellCommand,
         name='copy_mozconfig',
         command=['cp', mozconfig, 'build/.mozconfig'],
         description=['copy mozconfig'],
         workdir='.'
        )

    def addPreTestSteps(self):
        pass

    def addPostTestSteps(self):
        pass

class CodeCoverageFactory(UnittestBuildFactory):
    def addCopyMozconfigStep(self):
        config_dir_map = {
                'linux': 'linux/%s/codecoverage' % self.branchName,
                'macosx': 'macosx/%s/codecoverage' % self.branchName,
                'win32': 'win32/%s/codecoverage' % self.branchName,
                }
        mozconfig = 'mozconfigs/%s/%s/mozconfig' % \
            (self.config_dir, config_dir_map[self.platform])

        self.addStep(ShellCommand,
         name='copy_mozconfig',
         command=['cp', mozconfig, 'build/.mozconfig'],
         description=['copy mozconfig'],
         workdir='.'
        )

    def addInitialSteps(self):
        # Always clobber code coverage builds
        self.addStep(ShellCommand,
         name='rm_builddir',
         command=['rm', '-rf', 'build'],
         workdir=".",
         timeout=30*60,
        )
        UnittestBuildFactory.addInitialSteps(self)

    def addPreTestSteps(self):
        self.addStep(ShellCommand,
         name='mv_bin_original',
         command=['mv','bin','bin-original'],
         workdir="build/%s/dist" % self.objdir,
        )
        self.addStep(ShellCommand,
         name='jscoverage_bin',
         command=['jscoverage', '--mozilla',
                  '--no-instrument=defaults',
                  '--no-instrument=greprefs.js',
                  '--no-instrument=chrome/browser/content/browser/places/treeView.js',
                  'bin-original', 'bin'],
         workdir="build/%s/dist" % self.objdir,
        )

    def addPostTestSteps(self):
        self.addStep(ShellCommand,
         name='lcov_app_info',
         command=['lcov', '-c', '-d', '.', '-o', 'app.info'],
         workdir="build/%s" % self.objdir,
        )
        self.addStep(ShellCommand,
         name='rm_cc_html',
         command=['rm', '-rf', 'codecoverage_html'],
         workdir="build",
        )
        self.addStep(ShellCommand,
         name='mkdir_cc_html',
         command=['mkdir', 'codecoverage_html'],
         workdir="build",
        )
        self.addStep(ShellCommand,
         name='generate_html',
         command=['genhtml', '../%s/app.info' % self.objdir],
         workdir="build/codecoverage_html",
        )
        self.addStep(ShellCommand,
         name='cp_cc_html',
         command=['cp', '%s/dist/bin/application.ini' % self.objdir, 'codecoverage_html'],
         workdir="build",
        )
        tarfile = "codecoverage-%s.tar.bz2" % self.branchName
        self.addStep(ShellCommand,
         name='tar_cc_html',
         command=['tar', 'jcvf', tarfile, 'codecoverage_html'],
         workdir="build",
        )

        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_PATH': '/home/ftp/pub/firefox/nightly/experimental/codecoverage'})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey
        if 'POST_UPLOAD_CMD' in uploadEnv:
            del uploadEnv['POST_UPLOAD_CMD']
        self.addStep(ShellCommand,
         name='upload_tar',
         env=uploadEnv,
         command=['python', 'build/upload.py', tarfile],
         workdir="build",
        )

        # Tar up and upload the js report
        tarfile = "codecoverage-%s-jsreport.tar.bz2" % self.branchName
        self.addStep(ShellCommand,
         name='tar_cc_jsreport',
         command=['tar', 'jcv', '-C', '%s/dist/bin' % self.objdir, '-f', tarfile, 'jscoverage-report'],
         workdir="build",
        )
        self.addStep(ShellCommand,
         name='upload_jsreport',
         env=uploadEnv,
         command=['python', 'build/upload.py', tarfile],
         workdir="build",
        )

        # And the logs too
        tarfile = "codecoverage-%s-logs.tar" % self.branchName
        self.addStep(ShellCommand,
         name='tar_cc_logs',
         command=['tar', 'cvf', tarfile, 'logs'],
         workdir="build",
        )
        self.addStep(ShellCommand,
         name='upload_logs',
         env=uploadEnv,
         command=['python', 'build/upload.py', tarfile],
         workdir="build",
        )

        # Clean up after ourselves
        self.addStep(ShellCommand,
         name='rm_builddir',
         command=['rm', '-rf', 'build'],
         workdir=".",
         timeout=30*60,
        )

    def addTestSteps(self):
        self.addStep(ShellCommand(
         command=['rm', '-rf', 'logs'],
         workdir="build",
        ))
        self.addStep(ShellCommand(
         command=['mkdir', 'logs'],
         workdir="build",
        ))

        commands = [
                ('check', ['make', '-k', 'check'], 10*60),
                ('xpcshell', ['make', 'xpcshell-tests'], 1*60*60),
                ('reftest', ['make', 'reftest'], 1*60*60),
                ('crashtest', ['make', 'crashtest'], 12*60*60),
                ('mochitest-chrome', ['make', 'mochitest-chrome'], 1*60*60),
                ('mochitest-browser-chrome', ['make', 'mochitest-browser-chrome'], 12*60*60),
                ]

        # This should be replaced with 'make mochitest-plain-serial'
        # or chunked calls once those are available.
        mochitest_dirs = ['browser', 'caps', 'content', 'docshell', 'dom',
                'editor', 'embedding', 'extensions', 'fonts', 'intl', 'js',
                'layout', 'MochiKit_Unit_Tests', 'modules', 'parser',
                'toolkit', 'uriloader',]

        for test_dir in mochitest_dirs:
            commands.append(
                ('mochitest-plain-%s' % test_dir,
                 ['make', 'TEST_PATH=%s' % test_dir, 'mochitest-plain'],
                 4*60*60,)
                )

        if self.run_a11y:
            commands.append(
                ('mochitest-a11y', ['make', 'mochitest-a11y'], 4*60*60),
            )

        for name, command, timeout in commands:
            real_command = " ".join(command)
            real_command += " 2>&1 | bzip2 > ../logs/%s.log.bz2" % name
            self.addStep(ShellCommand,
             name=name,
             command=['bash', '-c', real_command],
             workdir="build/%s" % self.objdir,
             timeout=timeout,
            )

class L10nVerifyFactory(ReleaseFactory):
    def __init__(self, cvsroot, stagingServer, productName, version,
                 buildNumber, oldVersion, oldBuildNumber, verifyDir='verify',
                 linuxExtension='bz2', buildSpace=14, **kwargs):
        # MozillaBuildFactory needs the 'repoPath' argument, but we don't
        ReleaseFactory.__init__(self, repoPath='nothing', buildSpace=buildSpace,
                                **kwargs)

        verifyDirVersion = 'tools/release/l10n'

        # Remove existing verify dir
        self.addStep(ShellCommand,
         name='rm_verify_dir',
         description=['remove', 'verify', 'dir'],
         descriptionDone=['removed', 'verify', 'dir'],
         command=['rm', '-rf', verifyDir],
         workdir='.',
         haltOnFailure=True,
        )

        self.addStep(ShellCommand,
         name='mkdir_verify',
         description=['(re)create', 'verify', 'dir'],
         descriptionDone=['(re)created', 'verify', 'dir'],
         command=['bash', '-c', 'mkdir -p ' + verifyDirVersion],
         workdir='.',
         haltOnFailure=True,
        )

        # Download current release
        self.addStep(ShellCommand,
         name='download_current_release',
         description=['download', 'current', 'release'],
         descriptionDone=['downloaded', 'current', 'release'],
         command=['rsync',
                  '-Lav',
                  '-e', 'ssh',
                  '--exclude=*.asc',
                  '--exclude=source',
                  '--exclude=xpi',
                  '--exclude=unsigned',
                  '--exclude=update',
                  '--exclude=*.crashreporter-symbols.zip',
                  '--exclude=*.tests.tar.bz2',
                  '%s:/home/ftp/pub/%s/nightly/%s-candidates/build%s/*' %
                   (stagingServer, productName, version, str(buildNumber)),
                  '%s-%s-build%s/' % (productName,
                                      version,
                                      str(buildNumber))
                  ],
         workdir=verifyDirVersion,
         haltOnFailure=True,
         timeout=60*60
        )

        # Download previous release
        self.addStep(ShellCommand,
         name='download_previous_release',
         description=['download', 'previous', 'release'],
         descriptionDone =['downloaded', 'previous', 'release'],
         command=['rsync',
                  '-Lav',
                  '-e', 'ssh',
                  '--exclude=*.asc',
                  '--exclude=source',
                  '--exclude=xpi',
                  '--exclude=unsigned',
                  '--exclude=update',
                  '--exclude=*.crashreporter-symbols.zip',
                  '--exclude=*.tests.tar.bz2',
                  '%s:/home/ftp/pub/%s/nightly/%s-candidates/build%s/*' %
                   (stagingServer,
                    productName,
                    oldVersion,
                    str(oldBuildNumber)),
                  '%s-%s-build%s/' % (productName,
                                      oldVersion,
                                      str(oldBuildNumber))
                  ],
         workdir=verifyDirVersion,
         haltOnFailure=True,
         timeout=60*60
        )

        currentProduct = '%s-%s-build%s' % (productName,
                                            version,
                                            str(buildNumber))
        previousProduct = '%s-%s-build%s' % (productName,
                                             oldVersion,
                                             str(oldBuildNumber))

        for product in [currentProduct, previousProduct]:
            self.addStep(ShellCommand,
                         name='recreate_product_dir',
                         description=['(re)create', 'product', 'dir'],
                         descriptionDone=['(re)created', 'product', 'dir'],
                         command=['bash', '-c', 'mkdir -p %s/%s' % (verifyDirVersion, product)],
                         workdir='.',
                         haltOnFailure=True,
                        )
            self.addStep(ShellCommand,
                         name='verify_l10n',
                         description=['verify', 'l10n', product],
                         descriptionDone=['verified', 'l10n', product],
                         command=["bash", "-c",
                                  "./verify_l10n.sh " + product],
                         workdir=verifyDirVersion,
                         haltOnFailure=True,
                        )

        self.addStep(L10nVerifyMetaDiff,
                     currentProduct=currentProduct,
                     previousProduct=previousProduct,
                     workdir=verifyDirVersion,
                     )



class MobileBuildFactory(MozillaBuildFactory):
    def __init__(self, configRepoPath, mobileRepoPath, platform,
                 configSubDir, mozconfig, objdir="objdir",
                 stageUsername=None, stageSshKey=None, stageServer=None,
                 stageBasePath=None, stageGroup=None,
                 baseUploadDir=None, baseWorkDir='build', nightly=False,
                 clobber=False, env=None,
                 mobileRevision='default',
                 mozRevision='default', **kwargs):
        """
    mobileRepoPath: the path to the mobileRepo (mobile-browser)
    platform: the mobile platform (linux-arm, winmo-arm)
    baseWorkDir: the path to the default slave workdir
        """
        MozillaBuildFactory.__init__(self, **kwargs)
        self.configRepository = self.getRepository(configRepoPath)
        self.mobileRepository = self.getRepository(mobileRepoPath)
        self.mobileBranchName = self.getRepoName(self.mobileRepository)
        self.baseWorkDir = baseWorkDir
        self.configSubDir = configSubDir
        self.env = env
        self.nightly = nightly
        self.objdir = objdir
        self.platform = platform
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey
        self.stageServer = stageServer
        self.stageBasePath = stageBasePath
        self.stageGroup = stageGroup
        self.mobileRevision = mobileRevision
        self.mozRevision = mozRevision
        self.mozconfig = 'configs/%s/%s/mozconfig' % (self.configSubDir,
                                                      mozconfig)

        if nightly:
            self.clobber = clobber = True
        else:
            self.clobber = clobber

        if baseUploadDir is None:
            self.baseUploadDir = self.mobileBranchName
        else:
            self.baseUploadDir = baseUploadDir

        self.mozChangesetLink = '<a href=%s/rev' % (self.repository)
        self.mozChangesetLink += '/%(hg_revision)s title="Built from Mozilla revision %(hg_revision)s">moz:%(hg_revision)s</a>'
        self.mobileChangesetLink = '<a href=%s/rev' % (self.mobileRepository)
        self.mobileChangesetLink += '/%(hg_revision)s title="Built from Mobile revision %(hg_revision)s">mobile:%(hg_revision)s</a>'

    def addHgPullSteps(self, repository=None,
                       targetDirectory=None, workdir=None,
                       cloneTimeout=60*20,
                       revision='default',
                       changesetLink=None):
        assert (repository and workdir)
        if (targetDirectory == None):
            targetDirectory = self.getRepoName(repository)

        self.addStep(ShellCommand,
            name='checkout',
            command=['bash', '-c',
                     'if [ ! -d %s ]; then hg clone %s %s; fi' %
                     (targetDirectory, repository, targetDirectory)],
            workdir=workdir,
            description=['checking', 'out', targetDirectory],
            descriptionDone=['checked', 'out', targetDirectory],
            timeout=cloneTimeout
        )
        self.addStep(ShellCommand,
            name='hg_pull',
            command=['hg', 'pull'],
            workdir="%s/%s" % (workdir, targetDirectory),
            description=['pulling', targetDirectory],
            descriptionDone=['pulled', targetDirectory],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='hg_update',
            command=['hg', 'update', '-C', '-r', revision],
            workdir="%s/%s" % (workdir, targetDirectory),
            description=['updating', targetDirectory],
            descriptionDone=['updated', targetDirectory],
            haltOnFailure=True
        )
        if changesetLink:
            self.addStep(GetHgRevision(
                workdir='%s/%s' % (workdir, targetDirectory)
            ))
            self.addStep(OutputStep(
                name='tinderboxprint_changeset',
                data=['TinderboxPrint:', WithProperties(changesetLink)]
            ))

    def getMozconfig(self):
        self.addStep(ShellCommand,
            name='rm_configs',
            command=['rm', '-rf', 'configs'],
            workdir=self.baseWorkDir,
            description=['removing', 'configs'],
            descriptionDone=['remove', 'configs'],
            haltOnFailure=True
        )
        self.addHgPullSteps(repository=self.configRepository,
                            workdir=self.baseWorkDir,
                            targetDirectory='configs')
        self.addStep(ShellCommand,
            name='copy_mozconfig',
            command=['cp', self.mozconfig,
                     '%s/.mozconfig' % self.branchName],
            workdir=self.baseWorkDir,
            description=['copying', 'mozconfig'],
            descriptionDone=['copied', 'mozconfig'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='cat_mozconfig',
            command=['cat', '.mozconfig'],
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['cat', 'mozconfig']
        )

    def addPreBuildSteps(self):
        pass

    def addBaseRepoSteps(self):
        self.addHgPullSteps(repository=self.repository,
                            workdir=self.baseWorkDir,
                            changesetLink=self.mozChangesetLink,
                            revision=self.mozRevision,
                            cloneTimeout=60*30)
        self.addHgPullSteps(repository=self.mobileRepository,
                            workdir='%s/%s' % (self.baseWorkDir,
                                               self.branchName),
                            changesetLink=self.mobileChangesetLink,
                            revision=self.mobileRevision,
                            targetDirectory='mobile')

    def addUploadSteps(self, platform):
        self.addStep(SetProperty,
            name="get_buildid",
            command=['python', 'config/printconfigsetting.py',
                     '%s/mobile/dist/bin/application.ini' % self.objdir,
                     'App', 'BuildID'],
            property='buildid',
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['getting', 'buildid'],
            descriptionDone=['got', 'buildid']
        )
        self.addStep(MozillaStageUpload,
            name="upload_to_stage",
            description=['upload','to','stage'],
            objdir="%s/%s" % (self.branchName, self.objdir),
            username=self.stageUsername,
            milestone=self.baseUploadDir,
            remoteHost=self.stageServer,
            remoteBasePath=self.stageBasePath,
            platform=platform,
            group=self.stageGroup,
            packageGlob=self.packageGlob,
            sshKey=self.stageSshKey,
            uploadCompleteMar=False,
            releaseToLatest=self.nightly,
            releaseToDated=self.nightly,
            releaseToTinderboxBuilds=True,
            tinderboxBuildsDir=self.baseUploadDir,
            remoteCandidatesPath=self.stageBasePath,
            dependToDated=True,
            workdir='%s/%s/%s' % (self.baseWorkDir, self.branchName,
                                        self.objdir)
        )


class MobileDesktopBuildFactory(MobileBuildFactory):
    def __init__(self, packageGlob="-r mobile/dist/*.tar.bz2 " +
                 "xulrunner/dist/*.tar.bz2", uploadPlatform=None,
                 **kwargs):
        """This class creates a desktop fennec build.  -r in package glob
        is to ensure that all files are uploaded as this is the first
        option given to scp.  hack alert!"""
        MobileBuildFactory.__init__(self, **kwargs)
        self.packageGlob = packageGlob
        if uploadPlatform is not None:
            self.uploadPlatform = uploadPlatform
        else:
            self.uploadPlatform = self.platform.replace('-i686', '')

        self.addPreCleanSteps()
        self.addBaseRepoSteps()
        self.getMozconfig()
        self.addPreBuildSteps()
        self.addBuildSteps()
        self.addPackageSteps()
        self.addUploadSteps(platform=self.uploadPlatform)
        if self.triggerBuilds:
            self.addTriggeredBuildsSteps()
        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def addPreCleanSteps(self):
        self.addStep(ShellCommand,
                name='rm_cltbld_logs',
                command='rm -f /tmp/*_cltbld.log',
                description=['removing', 'log', 'file'],
                workdir=self.baseWorkDir
            )
        self.addStep(ShellCommand,
                name='clobber_%s_dir' % self.branchName,
                command=['rm', '-rf', self.branchName],
                description=['clobber', 'build'],
                timeout=60*60,
                workdir=self.baseWorkDir
            )

    def addBuildSteps(self):
        self.addStep(ShellCommand,
                name='compile',
                command=['make', '-f', 'client.mk', 'build'],
                description=['compile'],
                workdir=self.baseWorkDir + "/" +  self.branchName,
                env=self.env,
                haltOnFailure=True
            )

    def addPackageSteps(self):
        if self.platform.startswith("linux"):
            self.addStep(ShellCommand,
                name='make_xr_pkg',
                command=['make', 'package'],
                workdir='%s/%s/%s/xulrunner' % (self.baseWorkDir,
                    self.branchName, self.objdir),
                description=['make', 'xr', 'package'],
                env=self.env,
                haltOnFailure=True,
            )
        self.addStep(ShellCommand,
            name='make_mobile_pkg',
            command=['make', 'package'],
            workdir='%s/%s/%s/mobile' % (self.baseWorkDir,
            self.branchName, self.objdir),
            description=['make', 'mobile', 'package'],
            env=self.env,
            haltOnFailure=True,
        )
        if self.platform.startswith("linux"):
            self.addStep(ShellCommand,
                name='make_pkg_tests',
                command=['make', 'package-tests'],
                workdir='%s/%s/%s/xulrunner' % (self.baseWorkDir,
                    self.branchName, self.objdir),
                env=self.env,
                haltOnFailure=True,
            )

class MaemoBuildFactory(MobileBuildFactory):
    def __init__(self, baseBuildDir, scratchboxPath="/scratchbox/moz_scratchbox",
                 multiLocale = False,
                 l10nRepoPath = 'l10n-central',
                 compareLocalesRepoPath = 'build/compare-locales',
                 compareLocalesTag = 'RELEASE_AUTOMATION',
                 packageGlob="mobile/dist/*.tar.bz2 " +
                 "xulrunner/xulrunner/*.deb mobile/mobile/*.deb " +
                 "xulrunner/dist/*.tar.bz2",
                 l10nTag='default',
                 mergeLocales=True,
                 locales=None,
                 **kwargs):
        MobileBuildFactory.__init__(self, **kwargs)
        self.baseBuildDir = baseBuildDir
        self.packageGlob = packageGlob
        self.scratchboxPath = scratchboxPath
        self.multiLocale = multiLocale
        self.l10nRepoPath = l10nRepoPath
        self.l10nTag = l10nTag
        self.locales = locales
        self.mergeLocales = mergeLocales

        self.addPreCleanSteps()
        self.addBaseRepoSteps()
        self.getMozconfig()
        self.addPreBuildSteps()
        if self.multiLocale:
            # In the multi-locale scenario we build and upload the single-locale
            # before the multi-locale. This packageGlob will be used to move packages
            # into the "en-US" directory before uploading it and later on the 
            # multi-locale overwrites it in addMultiLocaleSteps(...) 
            self.packageGlob = "mobile/dist/*.tar.bz2 mobile/mobile/*.deb"
            self.compareLocalesRepo = self.getRepository(compareLocalesRepoPath)
            self.compareLocalesTag = compareLocalesTag
            self.addStep(ShellCommand,
                name='create_dir_l10n',
                command=['mkdir', '-p', self.l10nRepoPath],
                workdir='%s' % self.baseWorkDir,
                description=['create', 'l10n', 'dir']
            )
            self.addBuildSteps(extraEnv="L10NBASEDIR='../../../%s'" % self.l10nRepoPath)
            # This will package the en-US single-locale build (no xulrunner)
            self.addPackageSteps()
            self.uploadEnUS()
            self.useProgress = False
        else: # Normal single-locale nightly like Electrolysis and Tracemonkey
            self.addBuildSteps()
            self.addPackageSteps(packageXulrunner=True)
            self.addUploadSteps(platform='linux')
        
        if self.triggerBuilds:
            self.addTriggeredBuildsSteps()

        if self.multiLocale:
            self.nonMultiLocaleStepsLength = len(self.steps)

        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def newBuild(self, requests):
        if self.multiLocale:
            self.addMultiLocaleSteps(requests, 'locales')
        return BuildFactory.newBuild(self, requests)
        
    def addPreCleanSteps(self):
        self.addStep(ShellCommand,
            name='rm_logfile',
            command = 'rm -f /tmp/*_cltbld.log',
            description=['removing', 'logfile'],
            descriptionDone=['removed', 'logfile']
        )
        if self.clobber:
            self.addStep(ShellCommand,
                name='clobber_%s_dir' % self.branchName,
                command=['rm', '-rf', self.branchName],
                env=self.env,
                workdir=self.baseWorkDir,
                timeout=60*60
            )
            if self.multiLocale:
                self.addStep(ShellCommand,
                    name='clobber_l10n_dir',
                    command=['rm', '-rf', self.l10nRepoPath],
                    env=self.env,
                    workdir=self.baseWorkDir,
                    timeout=10*60
                )
        else:
            self.addStep(ShellCommand,
                name='rm_old_builds',
                command=['bash', '-c', 'rm -rf %s/%s/mobile/dist/fennec* ' %
                         (self.branchName, self.objdir) +
                         '%s/%s/xulrunner/xulrunner/*.deb ' %
                         (self.branchName, self.objdir) +
                         '%s/%s/xulrunner/dist/*.tar.bz2 ' %
                         (self.branchName, self.objdir) +
                         '%s/%s/mobile/mobile/*.deb' %
                         (self.branchName, self.objdir)],
                workdir=self.baseWorkDir,
                description=['removing', 'old', 'builds'],
                descriptionDone=['removed', 'old', 'builds']
            )

    def addBuildSteps(self, extraEnv=''):
        self.addStep(ShellCommand,
            name='compile',
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s/%s' % (self.baseBuildDir, self.branchName),
                     'make -f client.mk build %s' %  extraEnv],
            description=['compile'],
            env={'PKG_CONFIG_PATH': '/usr/lib/pkgconfig:/usr/local/lib/pkgconfig'},
            haltOnFailure=True
        )

    def addPackageSteps(self, multiLocale=False, packageXulrunner=False):
        extraArgs=''
        if multiLocale:
            extraArgs='AB_CD=multi'
        self.addStep(ShellCommand,
            name='make_pkg',
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s/%s/%s/mobile' % \
                     (self.baseBuildDir, self.branchName, self.objdir),
                     'make package', extraArgs],
            description=['make', 'package'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='make_mobile_deb',
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s/%s/%s/mobile' % \
                     (self.baseBuildDir, self.branchName, self.objdir),
                     'make deb', extraArgs],
            description=['make', 'mobile', 'deb'],
            haltOnFailure=True
        )
        # Build xulrunner for multi-locale nightly builds, dependent builds
        # and nightly builds which are not multi-locale like Electrolysis and Tracemonkey 
        if packageXulrunner or not self.nightly:
            self.addStep(ShellCommand,
                name='make_pkg_tests',
                command=[self.scratchboxPath, '-p', '-d',
                         'build/%s/%s/%s/xulrunner' % \
                         (self.baseBuildDir, self.branchName, self.objdir),
                         'make package-tests PYTHON=python2.5', extraArgs],
                description=['make', 'package-tests'],
                haltOnFailure=True
            )
            self.addStep(ShellCommand,
                name='make_xulrunner_deb',
                command=[self.scratchboxPath, '-p', '-d',
                         'build/%s/%s/%s/xulrunner' % \
                         (self.baseBuildDir, self.branchName, self.objdir),
                         'make deb', extraArgs],
                description=['make', 'xulrunner', 'deb'],
                haltOnFailure=True
            )
    
    def prepUpload(self, localeDir='en-US', uploadDir=None):
        """This function is only called by the nightly and dependent
        single-locale build since we want to upload the localeDir
        subdirectory as a whole."""
        # uploadDir allows you to use a nested directory structure in
        # localeDir (e.g. maemo/en-US) and scp -r maemo instead of
        # scp -r maemo/en-US, which loses that directory structure.
        if not uploadDir:
            uploadDir=localeDir
        self.addStep(ShellCommand,
            name='prepare_upload',
            command=['mkdir', '-p', localeDir],
            haltOnFailure=True,
            workdir='%s/%s/%s/mobile/dist' % (self.baseWorkDir, self.branchName, self.objdir)
        )
        self.addStep(ShellCommand,
            name='mv_binaries',
            command=['sh', '-c', 'mv %s mobile/dist/%s' % (self.packageGlob,
                                                           localeDir)],
            workdir='%s/%s/%s' % (self.baseWorkDir, self.branchName, self.objdir)
        )
        # Now that we have moved all the packages that we want under localeDir
        # let's indicate that we want to upload the whole directory
        self.packageGlob = '-r mobile/dist/%s' % uploadDir

    def uploadEnUS(self):
        self.prepUpload(localeDir='en-US')
        self.addUploadSteps(platform='linux')

    def uploadMulti(self):
        self.addUploadSteps(platform='linux')

    def addMultiLocaleSteps(self, requests=None, propertyName=None):
        if self.locales:
            locales = self.locales
        else:
            req = requests[-1]
            # get the list of locales that has been added by the scheduler
            locales = req.properties.getProperty(propertyName)

        # Drop all previous multi-locale steps, to fix bug 531873.
        self.steps = self.steps[:self.nonMultiLocaleStepsLength]
        # remove all packages that have been created by the single locale build
        self.addStep(ShellCommand,
            name='rm_packages',
            command=['sh', '-c', 'rm %s' % self.packageGlob],
            description=['remove single-locale packages'],
            workdir="%s/%s/%s" % (self.baseWorkDir,
                                  self.branchName,
                                  self.objdir),
            flunkOnFailure=False,
            warnOnFailure=False,
        )
        self.compareLocalesSetup()
        for locale in locales:
            self.checkOutLocale(locale)
            self.compareLocales(locale)
            self.addChromeLocale(locale)
        # Let's package the multi-locale build and upload it
        self.addPackageSteps(multiLocale=True, packageXulrunner=True)
        self.packageGlob="mobile/dist/fennec*.tar.bz2 mobile/mobile/fennec*.deb " + \
                         "xulrunner/dist/*.tar.bz2 xulrunner/xulrunner/xulrunner*.deb"
        self.uploadMulti()
        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def compareLocalesSetup(self):
        self.addStep(ShellCommand,
         name='rm_compare_locales',
         command=['rm', '-rf', 'compare-locales'],
         description=['remove', 'compare-locales'],
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='clone_compare_locales',
         command=['hg', 'clone', self.compareLocalesRepo, 'compare-locales'],
         description=['checkout', 'compare-locales'],
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='update_compare_locales',
         command=['hg', 'up', '-C', '-r', self.compareLocalesTag],
         description='update compare-locales',
         workdir='%s/compare-locales' % self.baseWorkDir,
         haltOnFailure=True
        )

    def checkOutLocale(self, locale):
        self.addStep(ShellCommand,
         name='get_locale_src_%s' % locale,
         command=['sh', '-c',
          WithProperties('if [ -d '+locale+' ]; then ' +
                         'hg -R '+locale+' pull -r '+self.l10nTag+' ; ' +
                         'else ' +
                         'hg clone ' +
                         'http://'+self.hgHost+'/'+self.l10nRepoPath+\
                           '/'+locale+'/ ; ' +
                         'hg -R '+locale+' up -r '+self.l10nTag+' ; '
                         'fi ')],
         descriptionDone="locale source",
         timeout=5*60, # 5 minutes
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.l10nRepoPath)
        )

    def compareLocales(self, locale):
        objdirAbsPath = "%s/%s/%s" % (self.baseWorkDir, self.branchName, self.objdir)
        if self.mergeLocales:
            mergeLocaleOptions = ['-m', '%s/merged' % objdirAbsPath]
        else:
            mergeLocaleOptions = []
        self.addStep(ShellCommand,
         name='rm_merged_%s' % locale,
         command=['rm', '-rf', 'merged'],
         description=['remove', 'merged'],
         workdir=objdirAbsPath,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         name='run_compare_locales_%s' % locale,
         command=['python',
                  '%s/compare-locales/scripts/compare-locales' % self.baseWorkDir] +
                  mergeLocaleOptions +
                  ["l10n.ini",
                  "%s/%s" % (self.baseWorkDir, self.l10nRepoPath),
                  locale],
         description='comparing %s' % locale,
         env={'PYTHONPATH': ['%s/compare-locales/lib' % self.baseWorkDir]},
         flunkOnFailure=False,
         warnOnFailure=True,
         workdir="%s/%s/mobile/locales" % \
                (self.baseWorkDir, self.branchName),
        )

    def addChromeLocale(self, locale):
        # This is relative to scratchbox's home directory
        objdir = 'build/%s/%s/%s' % (self.baseBuildDir, self.branchName, self.objdir)
        objdirAbsPath = '/home/cltbld/%s' % objdir
        if self.mergeLocales:
            mergeDirArgs = ['LOCALE_MERGEDIR=%s/merged' % objdirAbsPath]
        else:
            mergeDirArgs = []
        # TODO: Move '/home/cltbld' to our config files
        # the absolute path (from within scratchbox)  for the objdir
        self.addStep(ShellCommand,
            name='make_locale_chrome_%s' % locale,
            command=[self.scratchboxPath, '-p', '-d',
                     '%s/mobile/mobile/locales' % objdir,
                     'make chrome-%s' % locale] + mergeDirArgs,
            env = self.env,
            haltOnFailure=False,
            description=['make','chrome-%s' % locale],
        )

class MaemoReleaseBuildFactory(MaemoBuildFactory):
    def __init__(self, env, **kwargs):
        env = env.copy()
        MaemoBuildFactory.__init__(self, env=env, nightly=False,
                                   clobber=True, **kwargs)
        assert (self.stageUsername)

    def uploadEnUS(self):
        self.prepUpload(localeDir='maemo/en-US', uploadDir='maemo')
        self.addUploadSteps(platform='linux')
        
    def uploadMulti(self):
        self.prepUpload(localeDir='maemo/multi', uploadDir='maemo')
        self.addStep(SetProperty,
            command=['python', 'config/printconfigsetting.py',
                     '%s/mobile/dist/bin/application.ini' % self.objdir,
                     'App', 'BuildID'],
            property='buildid',
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['getting', 'buildid'],
            descriptionDone=['got', 'buildid']
        )
        self.addStep(ShellCommand,
         name='echo_buildID',
         command=['bash', '-c',
                  WithProperties('echo buildID=%(buildid)s > ' + \
                                'maemo_info.txt')],
         workdir='%s/%s/%s/mobile/dist' % (self.baseWorkDir, self.branchName,
                                           self.objdir)
        )
        self.packageGlob = '%s mobile/dist/maemo_info.txt' % self.packageGlob
        self.addUploadSteps(platform='linux')

    def addUploadSteps(self, platform):
        self.addStep(SetProperty,
            command=['python', 'config/printconfigsetting.py',
                     '%s/mobile/dist/bin/application.ini' % self.objdir,
                     'App', 'BuildID'],
            property='buildid',
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['getting', 'buildid'],
            descriptionDone=['got', 'buildid']
        )
        self.addStep(MozillaStageUpload,
            objdir="%s/%s" % (self.branchName, self.objdir),
            username=self.stageUsername,
            milestone=self.baseUploadDir,
            remoteHost=self.stageServer,
            remoteBasePath=self.stageBasePath,
            platform=platform,
            group=self.stageGroup,
            packageGlob=self.packageGlob,
            sshKey=self.stageSshKey,
            uploadCompleteMar=False,
            releaseToLatest=False,
            releaseToDated=False,
            releaseToTinderboxBuilds=False,
            releaseToCandidates=True,
            tinderboxBuildsDir=self.baseUploadDir,
            remoteCandidatesPath=self.stageBasePath,
            dependToDated=True,
            workdir='%s/%s/%s' % (self.baseWorkDir, self.branchName,
                                        self.objdir)
        )

class WinmoBuildFactory(MobileBuildFactory):
    def __init__(self,
                 packageGlob="xulrunner/dist/*.zip mobile/dist/*.zip mobile/dist/*.exe xulrunner/dist/*.tar.bz2",
                 createSnippet=False, downloadBaseURL=None,
                 ausUser=None, ausHost=None, ausBaseUploadDir=None,
                 updatePlatform='WINCE_arm-msvc', **kwargs):
        MobileBuildFactory.__init__(self, **kwargs)
        self.packageGlob = packageGlob
        self.createSnippet = createSnippet
        if self.createSnippet:
            assert downloadBaseURL and \
                   ausUser and \
                   ausHost and \
                   ausBaseUploadDir and \
                   updatePlatform
            self.packageGlob += " mobile/dist/update/*.complete.mar"
            self.downloadBaseURL = downloadBaseURL
            self.ausUser = ausUser
            self.ausHost = ausHost
            self.ausBaseUploadDir = ausBaseUploadDir
            self.updatePlatform = updatePlatform
            
            # this is a tad ugly because we need to python interpolation
            # as well as WithProperties
            # here's an example of what it translates to:
            # /opt/aus2/build/0/Firefox/mozilla2/WINNT_x86-msvc/2008010103/en-US
            self.ausFullUploadDir = '%s/%s/%%(buildid)s/en-US' % \
              (self.ausBaseUploadDir, self.updatePlatform)

        self.addPreCleanSteps()
        self.addBaseRepoSteps()
        self.getMozconfig()
        self.addPreBuildSteps()
        self.addBuildSteps()
        self.addPackageSteps()
        if self.createSnippet:
            self.addUpdateSteps()
        self.addUploadSteps(platform='win32')
        if self.triggerBuilds:
            self.addTriggeredBuildsSteps()
        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def addPreCleanSteps(self):
        if self.clobber:
            self.addStep(ShellCommand,
                name='clobber_%s_dir' % self.branchName,
                command=['rm', '-rf', self.branchName],
                env=self.env,
                workdir=self.baseWorkDir,
                timeout=60*60
            )
        else:
            self.addStep(ShellCommand,
                name='remove_old_builds',
                command = ['bash', '-c', 'rm -rf %s/%s/mobile/dist/*.zip ' %
                           (self.branchName, self.objdir) +
                           '%s/%s/mobile/dist/*.exe ' %
                           (self.branchName, self.objdir) +
                           '%s/%s/xulrunner/dist/*.zip' %
                           (self.branchName, self.objdir)],
                workdir=self.baseWorkDir,
                description=['removing', 'old', 'builds'],
                descriptionDone=['removed', 'old', 'builds']
            )

    def addBuildSteps(self):
        self.addStep(ShellCommand,
            name='compile',
            command=['make', '-f', 'client.mk', 'build'],
            description=['compile'],
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            env=self.env,
            haltOnFailure=True
        )

    def addPackageSteps(self):
        self.addStep(ShellCommand,
            name='make_pkg',
            command=['make', 'package'],
            workdir='%s/%s/%s/mobile' % (self.baseWorkDir, self.branchName,
                                         self.objdir),
            description=['make', 'package'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='make_installer',
            command=['make', 'installer'],
            workdir='%s/%s/%s/mobile' % (self.baseWorkDir, self.branchName,
                                         self.objdir),
            description=['make', 'installer'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='make_pkg_tests',
            command=['make', 'package-tests'],
            workdir='%s/%s/%s/xulrunner' % (self.baseWorkDir, self.branchName,
                                            self.objdir),
            description=['make', 'package-tests'],
            haltOnFailure=True
        )

    def addUpdateSteps(self):
        self.addStep(ShellCommand,
            name='make_update_pkg',
            command=['make', '-C', 'tools/update-packaging'],
            workdir='%s/%s/%s/mobile' % (self.baseWorkDir, self.branchName,
                                         self.objdir),
            description=['make', 'update', 'pkg'],
            haltOnFailure=True
        )
        self.addFilePropertiesSteps(filename='*.complete.mar',
                                    directory='%s/%s/%s/mobile/dist/update' % \
                                      (self.baseWorkDir,
                                       self.branchName,
                                       self.objdir),
                                    fileType='completeMar',
                                    haltOnFailure=True)
        self.addStep(SetProperty,
            name="get_buildid",
            command=['python', 'config/printconfigsetting.py',
                     '%s/mobile/dist/bin/application.ini' % self.objdir,
                     'App', 'BuildID'],
            property='buildid',
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['getting', 'buildid'],
            descriptionDone=['got', 'buildid']
        )
        self.addStep(SetProperty(
            name="get_app_version",
            command=['python', 'config/printconfigsetting.py',
                     '%s/mobile/dist/bin/application.ini' % self.objdir,
                     'App', 'Version'],
            property='appVersion',
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['getting', 'app', 'version'],
            descriptionDone=['got', 'app', 'version']
        ))
        self.addStep(CreateCompleteUpdateSnippet,
            objdir='%s/%s/%s' % (self.baseWorkDir, self.branchName, self.objdir),
            milestone=self.baseUploadDir,
            baseurl='%s/nightly' % self.downloadBaseURL,
            hashType=self.hashType
        )
        self.addStep(ShellCommand,
            name='cat_complete_snippet',
            description=['cat','complete','snippet'],
            command=['cat','complete.update.snippet'],
            workdir='%s/%s/%s/dist/update' % (self.baseWorkDir, self.branchName, self.objdir),
        )

    def addUploadSteps(self, platform='win32'):
        MobileBuildFactory.addUploadSteps(self,platform=platform) 
        # ausFullUploadDir contains an interpolation of the buildid property.
        # We expect the property to be set by the parent call to
        # addUploadSteps()
        if self.createSnippet:
            self.addStep(ShellCommand,
                name='create_aus_updir',
                command=['ssh', '-l', self.ausUser, self.ausHost,
                         WithProperties('mkdir -p %s' % self.ausFullUploadDir)],
                description=['create', 'aus', 'upload', 'dir'],
                haltOnFailure=True
            )
            self.addStep(ShellCommand,
                name='upload_complete_snippet',
                command=['scp', '-o', 'User=%s' % self.ausUser,
                         'dist/update/complete.update.snippet',
                         WithProperties('%s:%s/complete.txt' % \
                           (self.ausHost, self.ausFullUploadDir))],
                workdir='%s/%s/%s' % (self.baseWorkDir, self.branchName, self.objdir),
                description=['upload', 'complete', 'snippet'],
                haltOnFailure=True
            )


class UnittestPackagedBuildFactory(MozillaBuildFactory):
    def __init__(self, platform, test_suites, env={}, productName='firefox',
            mochitest_leak_threshold=None, crashtest_leak_threshold=None,
            totalChunks=None, thisChunk=None, chunkByDir=None,
            **kwargs):
        self.platform = platform.split('-')[0]

        self.env = MozillaEnvironments['%s-unittest' % self.platform].copy()
        self.env['MINIDUMP_STACKWALK'] = getPlatformMinidumpPath(self.platform)
        self.env.update(env)
        self.productName = productName

        assert self.platform in ('linux', 'linux64', 'win32', 'macosx')

        MozillaBuildFactory.__init__(self, **kwargs)

        self.test_suites = test_suites
        self.leak_thresholds = {'mochitest-plain': mochitest_leak_threshold,
                                'crashtest': crashtest_leak_threshold}

        # Download the build
        def get_fileURL(build):
            fileURL = build.source.changes[-1].files[0]
            build.setProperty('fileURL', fileURL, 'DownloadFile')
            return fileURL
        self.addStep(DownloadFile(
         url_fn=get_fileURL,
         filename_property='build_filename',
         haltOnFailure=True,
         name="download build",
        ))

        # Download the tests
        def get_testURL(build):
            # If there is a second file in the changes object,
            # use that as the test harness to download
            if len(build.source.changes[-1].files) > 1:
                testURL = build.source.changes[-1].files[1]
                return testURL
            else:
                fileURL = build.getProperty('fileURL')
                suffixes = ('.tar.bz2', '.dmg', '.zip')
                testsURL = None
                for suffix in suffixes:
                    if fileURL.endswith(suffix):
                        testsURL = fileURL[:-len(suffix)] + '.tests.tar.bz2'
                        return testsURL
                if testsURL is None:
                    raise ValueError("Couldn't determine tests URL")
        self.addStep(DownloadFile(
         url_fn=get_testURL,
         filename_property='tests_filename',
         haltOnFailure=True,
         name='download tests',
        ))

        # Download the crash symbols
        def get_symbolsURL(build):
            fileURL = build.getProperty('fileURL')
            suffixes = ('.tar.bz2', '.dmg', '.zip')
            symbolsURL = None
            for suffix in suffixes:
                if fileURL.endswith(suffix):
                    symbolsURL = fileURL[:-len(suffix)] + '.crashreporter-symbols.zip'
                    return symbolsURL
            raise ValueError("Couldn't determine symbols URL")
        self.addStep(DownloadFile(
         url_fn=get_symbolsURL,
         filename_property='symbols_filename',
         name='download symbols',
         workdir='build/symbols',
        ))

        # Unpack the build
        self.addStep(UnpackFile(
         filename=WithProperties('%(build_filename)s'),
         scripts_dir='../tools/buildfarm/utils',
         haltOnFailure=True,
         name='unpack build',
        ))

        # Unpack the tests
        self.addStep(UnpackFile(
         filename=WithProperties('%(tests_filename)s'),
         haltOnFailure=True,
         name='unpack tests',
        ))

        # Unpack the symbols
        self.addStep(UnpackFile(
         filename=WithProperties('%(symbols_filename)s'),
         name='unpack symbols',
         workdir='build/symbols',
        ))

        # Find the application binary!
        if self.platform == "macosx":
            self.addStep(FindFile(
             filename="%s-bin" % self.productName,
             directory=".",
             filetype="file",
             max_depth=4,
             property_name="exepath",
             name="Find executable",
            ))
        elif self.platform == "win32":
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="%s/%s.exe" % (self.productName, self.productName),
            ))
        else:
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="%s/%s-bin" % (self.productName, self.productName),
            ))

        def get_exedir(build):
            return os.path.dirname(build.getProperty('exepath'))
        self.addStep(SetBuildProperty(
         property_name="exedir",
         value=get_exedir,
        ))

        # Set up the stack walker
        if self.platform == 'win32':
            self.addStep(SetProperty,
             command=['bash', '-c', 'pwd -W'],
             property='toolsdir',
             workdir='tools'
            )
        else:
            self.addStep(SetProperty,
             command=['bash', '-c', 'pwd'],
             property='toolsdir',
             workdir='tools'
            )

        # Figure out which revision we're running
        def get_build_info(rc, stdout, stderr):
            retval = {}
            stdout = "\n".join([stdout, stderr])
            m = re.search("^BuildID=(\w+)", stdout, re.M)
            if m:
                retval['buildid'] = m.group(1)
            m = re.search("^SourceStamp=(\w+)", stdout, re.M)
            if m:
                retval['revision'] = m.group(1)
            m = re.search("^SourceRepository=(\S+)", stdout, re.M)
            if m:
                retval['repo_path'] = m.group(1)
            return retval
        self.addStep(SetProperty,
         command=['cat', WithProperties('%(exedir)s/application.ini')],
         workdir='build',
         extract_fn=get_build_info,
         name='get build info',
        )

        changesetLink = '<a href=%(repo_path)s/rev/%(revision)s' + \
                ' title="Built from revision %(revision)s">rev:%(revision)s</a>'
        self.addStep(OutputStep(
         name='tinderboxprint_changeset',
         data=['TinderboxPrint:', WithProperties(changesetLink)],
        ))

        # Run them!
        for suite in self.test_suites:
            leak_threshold = self.leak_thresholds.get(suite, None)
            if suite.startswith('mochitest'):
                variant = suite.split('-', 1)[1]
                self.addStep(unittest_steps.MozillaPackagedMochitests(
                 variant=variant,
                 env=self.env,
                 symbols_path='symbols',
                 leakThreshold=leak_threshold,
                 chunkByDir=chunkByDir,
                 totalChunks=totalChunks,
                 thisChunk=thisChunk,
                 maxTime=90*60, # One and a half hours, to allow for slow minis
                ))
            elif suite == 'xpcshell':
                self.addStep(unittest_steps.MozillaPackagedXPCShellTests(
                 env=self.env,
                 symbols_path='symbols',
                 maxTime=60*60, # One Hour
                ))
            elif suite in ('reftest', 'crashtest', 'jsreftest'):
                self.addStep(unittest_steps.MozillaPackagedReftests(
                 suite=suite,
                 env=self.env,
                 leakThreshold=leak_threshold,
                 symbols_path='symbols',
                 maxTime=60*60, # One Hour
                ))

        if self.buildsBeforeReboot and self.buildsBeforeReboot > 0:
            self.addPeriodicRebootSteps()

    def addInitialSteps(self):
        self.addStep(ShellCommand,
         name='rm_builddir',
         command=['rm', '-rf', 'build'],
         workdir='.'
        )
        MozillaBuildFactory.addInitialSteps(self)

class TalosFactory(BuildFactory):
    """Create working talos build factory"""
    def __init__(self, OS, toolsDir, envName, buildBranch, branchName,
            configOptions, talosCmd, customManifest=None, customTalos=None,
            workdirBase=None, fetchSymbols=False, plugins=None, pageset=None,
            talosAddOns=[],
            cvsRoot=":pserver:anonymous@cvs-mirror.mozilla.org:/cvsroot"):

        BuildFactory.__init__(self)

        if workdirBase is None:
            workdirBase = "."

        self.workdirBase = workdirBase
        self.envName = envName
        self.OS = OS
        self.toolsDir = toolsDir
        self.buildBranch = buildBranch
        self.branchName = branchName
        self.configOptions = configOptions
        self.talosCmd = talosCmd
        self.customManifest = customManifest
        self.customTalos = customTalos
        self.cvsRoot = cvsRoot
        self.fetchSymbols = fetchSymbols
        self.plugins = plugins
        self.pageset = pageset
        self.talosAddOns = talosAddOns[:]
        self.exepath = None

        self.addCleanupSteps()
        self.addDmgInstaller()
        self.addDownloadBuildStep()
        self.addUnpackBuildSteps()
        self.addGetBuildInfoStep()
        if fetchSymbols and self.OS <> 'linux64':
            self.addDownloadSymbolsStep()
        self.addSetupSteps()
        self.addUpdateConfigStep()
        self.addRunTestStep()
        self.addRebootStep()

    def addCleanupSteps(self):
        self.addStep(ShellCommand(
         name='cleanup',
         workdir=self.workdirBase,
         description="Cleanup",
         command='rm -vrf *',
         env=MozillaEnvironments[self.envName])
        )
        self.addStep(ShellCommand(
         name='create talos dir',
         workdir=self.workdirBase,
         description="talos dir creation",
         command='mkdir talos',
         env=MozillaEnvironments[self.envName])
        )

    def addDmgInstaller(self):
        if self.OS in ('leopard', 'tiger', 'snowleopard'):
            self.addStep(FileDownload(
             mastersrc="%s/buildfarm/utils/installdmg.sh" % self.toolsDir,
             slavedest="installdmg.sh",
             workdir=self.workdirBase,
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
         name="Download build",
        ))

    def addUnpackBuildSteps(self):
        self.addStep(UnpackFile(
         filename=WithProperties("%(filename)s"),
         workdir=self.workdirBase,
         name="Unpack build",
        ))
        if self.OS in ('xp', 'vista', 'win7'):
            self.addStep(ShellCommand(
             name='chmod_files',
             workdir=os.path.join(self.workdirBase, "firefox/"),
             flunkOnFailure=False,
             warnOnFailure=False,
             description="chmod files (see msys bug)",
             command=["chmod", "-v", "-R", "a+x", "."],
             env=MozillaEnvironments[self.envName])
            )
        if self.OS in ('tiger', 'leopard', 'snowleopard'):
            self.addStep(FindFile(
             workdir=os.path.join(self.workdirBase, "talos"),
             filename="firefox-bin",
             directory="..",
             max_depth=4,
             property_name="exepath",
             name="Find executable",
             filetype="file",
            ))
        elif self.OS in ('xp', 'vista', 'win7'):
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="../firefox/firefox"
            ))
        else:
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="../firefox/firefox-bin"
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
        self.addStep(SetProperty,
         command=['cat', WithProperties('%(exedir)s/application.ini')],
         workdir=os.path.join(self.workdirBase, "talos"),
         extract_fn=get_build_info,
         name='get build info',
        )

        def check_sdk(step, cmd):
            txt = cmd.logs['stdio'].getText()
            m = re.search("MacOSX10\.5\.sdk", txt, re.M)
            if m :
                step.addCompleteLog('sdk-fail', 'TinderboxPrint: Skipping tests; can\'t run 10.5 based build on 10.4 slave')
                return FAILURE
            return SUCCESS
        if self.OS == "tiger":
            self.addStep(EvaluatingShellCommand(
                command=['cat', WithProperties('%(exedir)s/chrome/toolkit.jar')],
                workdir=os.path.join(self.workdirBase, "talos"),
                eval_fn=check_sdk,
                haltOnFailure=True,
                flunkOnFailure=False,
                name='check sdk okay'))
     
    def addSetupSteps(self):
        self.addStep(FileDownload(
         mastersrc="%s/buildfarm/maintenance/count_and_reboot.py" % self.toolsDir,
         slavedest="count_and_reboot.py",
         workdir=self.workdirBase,
        ))

        if self.customManifest:
            self.addStep(FileDownload(
             mastersrc=self.customManifest,
             slavedest="tp3.manifest",
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"))
            )

        if self.customTalos is None:
            self.addStep(ShellCommand(
             name='checkout_talos',
             command=["cvs", "-d", self.cvsRoot, "co", "-d", "talos",
                      "mozilla/testing/performance/talos"],
             workdir=self.workdirBase,
             description="checking out talos",
             haltOnFailure=True,
             flunkOnFailure=True,
             env=MozillaEnvironments[self.envName])
            )
            self.addStep(FileDownload(
             mastersrc="%s/buildfarm/utils/generate-tpcomponent.py" % self.toolsDir,
             slavedest="generate-tpcomponent.py",
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"))
            )
            self.addStep(ShellCommand(
             name='setup_pageloader',
             command=["python", "generate-tpcomponent.py"],
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
             description="setting up pageloader",
             haltOnFailure=True,
             flunkOnFailure=True,
             env=MozillaEnvironments[self.envName])
            )
        else:
            self.addStep(FileDownload(
             mastersrc=self.customTalos,
             slavedest=self.customTalos,
             workdir=self.workdirBase,
            ))
            self.addStep(UnpackFile(
             filename=self.customTalos,
             workdir=self.workdirBase,
            ))

        if self.plugins:
            self.addStep(FileDownload(
             mastersrc=self.plugins,
             slavedest=os.path.basename(self.plugins),
             workdir=os.path.join(self.workdirBase, "talos/base_profile"),
             blocksize=640*1024,
            ))
            self.addStep(UnpackFile(
             filename=os.path.basename(self.plugins),
             workdir=os.path.join(self.workdirBase, "talos/base_profile"),
            ))

        if self.pageset:
            self.addStep(FileDownload(
             mastersrc=self.pageset,
             slavedest=os.path.basename(self.pageset),
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
             blocksize=640*1024,
            ))
            self.addStep(UnpackFile(
             filename=os.path.basename(self.pageset),
             workdir=os.path.join(self.workdirBase, "talos/page_load_test"),
            ))

        for addOn in self.talosAddOns:
            self.addStep(FileDownload(
             mastersrc=addOn,
             slavedest=os.path.basename(addOn),
             workdir=os.path.join(self.workdirBase, "talos"),
             blocksize=640*1024,
            ))
            self.addStep(UnpackFile(
             filename=os.path.basename(addOn),
             workdir=os.path.join(self.workdirBase, "talos"),
            ))

    def addDownloadSymbolsStep(self):
        def get_symbols_url(build):
            suffixes = ('.tar.bz2', '.dmg', '.zip')
            buildURL = build.getProperty('fileURL')

            for suffix in suffixes:
                if buildURL.endswith(suffix):
                    return buildURL[:-len(suffix)] + '.crashreporter-symbols.zip'

        self.addStep(DownloadFile(
         url_fn=get_symbols_url,
         filename_property="symbolsFile",
         workdir=self.workdirBase,
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

    def addUpdateConfigStep(self):
        self.addStep(talos_steps.MozillaUpdateConfig(
         workdir=os.path.join(self.workdirBase, "talos/"),
         branch=self.buildBranch,
         branchName=self.branchName,
         haltOnFailure=True,
         executablePath=self.exepath,
         addOptions=self.configOptions,
         env=MozillaEnvironments[self.envName],
         useSymbols=self.fetchSymbols)
        )

    def addRunTestStep(self):
        self.addStep(talos_steps.MozillaRunPerfTests(
         warnOnWarnings=True,
         workdir=os.path.join(self.workdirBase, "talos/"),
         timeout=21600,
         haltOnFailure=False,
         command=self.talosCmd,
         env=MozillaEnvironments[self.envName])
        )

    def addRebootStep(self):
        def do_disconnect(cmd):
            try:
                if 'SCHEDULED REBOOT' in cmd.logs['stdio'].getText():
                    return True
            except:
                pass
            return False
        self.addStep(DisconnectStep(
         name='reboot',
         flunkOnFailure=False,
         warnOnFailure=False,
         alwaysRun=True,
         workdir=self.workdirBase,
         description="reboot after 1 test run",
         command=["python", "count_and_reboot.py", "-f", "../talos_count.txt", "-n", "1", "-z"],
         force_disconnect=do_disconnect,
         env=MozillaEnvironments[self.envName],
        ))

class TryTalosFactory(TalosFactory):
    def addDownloadBuildStep(self):
        def get_url(build):
            url = build.source.changes[-1].files[0]
            return url
        self.addStep(DownloadFile(
         url_fn=get_url,
         url_property="fileURL",
         filename_property="filename",
         workdir=self.workdirBase,
         name="Download build",
         ignore_certs=True,
        ))

        def make_tinderbox_header(build):
            identifier = build.getProperty("filename").rsplit('-', 1)[0]
            # Grab the submitter out of the dir name. CVS and Mercurial builds
            # are a little different, so we need to try fairly hard to find
            # the e-mail address.
            dir = os.path.basename(os.path.dirname(build.getProperty("fileURL")))
            who = ''
            for section in dir.split('-'):
                if '@' in section:
                    who = section
                    break
            msg =  'TinderboxPrint: %s\n' % who
            msg += 'TinderboxPrint: %s\n' % identifier
            return msg
        self.addStep(OutputStep(data=make_tinderbox_header, log='header', name='echo_id'))

    def addDownloadSymbolsStep(self):
        def get_symbols_url(build):
            suffixes = ('.tar.bz2', '.dmg', '.zip')
            buildURL = build.getProperty('fileURL')

            for suffix in suffixes:
                if buildURL.endswith(suffix):
                    return buildURL[:-len(suffix)] + '.crashreporter-symbols.zip'

        self.addStep(DownloadFile(
         url_fn=get_symbols_url,
         filename_property="symbolsFile",
         workdir=self.workdirBase,
         name="Download symbols",
         ignore_certs=True,
         haltOnFailure=False,
         flunkOnFailure=False,
        ))
        self.addStep(ShellCommand(
         command=['mkdir', 'symbols'],
         workdir=self.workdirBase,
        ))
        self.addStep(UnpackFile(
         filename=WithProperties("../%(symbolsFile)s"),
         workdir="%s/symbols" % self.workdirBase,
         name="Unpack symbols",
         haltOnFailure=False,
         flunkOnFailure=False,
        ))


class MobileNightlyRepackFactory(BaseRepackFactory):
    extraConfigureArgs = []

    def __init__(self, enUSBinaryURL, hgHost=None,
                 nightly=True,
                 mobileRepoPath='mobile-browser',
                 project='fennec', baseWorkDir='build',
                 repoPath='mozilla-central', appName='mobile',
                 l10nRepoPath='l10n-central',
                 stageServer=None, stageUsername=None, stageGroup=None,
                 stageSshKey=None, stageBasePath=None,
                 packageGlob=None, platform=None,
                 baseUploadDir=None,
                 **kwargs):

        self.hgHost = hgHost
        self.nightly = nightly
        self.mobileRepoPath = mobileRepoPath
        self.mobileRepository = self.getRepository(mobileRepoPath)
        self.mobileBranchName = self.getRepoName(self.mobileRepository)
        self.enUSBinaryURL = enUSBinaryURL

        self.platform = platform
        self.hgHost = hgHost
        self.stageServer = stageServer
        self.stageUsername = stageUsername
        self.stageGroup = stageGroup
        self.stageSshKey = stageSshKey
        self.stageBasePath = stageBasePath
        self.packageGlob = packageGlob

        if baseUploadDir is None:
            self.baseUploadDir = self.mobileBranchName
        else:
            self.baseUploadDir = baseUploadDir

        # unused here but needed by BaseRepackFactory
        self.postUploadCmd = None

        self.env = {}

        BaseRepackFactory.__init__(self,
                                   project=project,
                                   appName=appName,
                                   repoPath=repoPath,
                                   l10nRepoPath=l10nRepoPath,
                                   stageServer=stageServer,
                                   stageUsername=stageUsername,
                                   stageSshKey=stageSshKey,
                                   baseWorkDir=baseWorkDir,
                                   hgHost=hgHost,
                                   **kwargs)

    def getSources(self):
        BaseRepackFactory.getSources(self)
        self.addStep(ShellCommand,
         name='enUS_mobile_source',
         command=['sh', '-c', 'if [ -d mobile ]; then ' +
                  'hg -R mobile pull -r '+self.l10nTag+' ; else ' +
                  'hg clone http://' + self.hgHost + '/' + self.mobileRepoPath +
                  ' mobile ; ' +
                  'fi && hg -R mobile update -r '+self.l10nTag],
         descriptionDone=['en-US', 'mobile', 'source'],
         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir),
         timeout=30*60 # 30 minutes
        )

    def updateSources(self):
        self.addStep(ShellCommand,
         name='update_workdir',
         command=['hg', 'up', '-C', '-r', self.l10nTag],
         description='update workdir',
         workdir=WithProperties(self.baseWorkDir + '/' + self.l10nRepoPath + \
                                '/%(locale)s'),
         haltOnFailure=True
        )
        self.addStep(SetProperty,
                     command=['hg', 'ident', '-i'],
                     haltOnFailure=True,
                     property='l10n_revision',
                     workdir=WithProperties(self.baseWorkDir + '/' + self.l10nRepoPath +
                                            '/%(locale)s')
                     )

    def getMozconfig(self):
        pass

    def downloadBuilds(self):
        self.addStep(ShellCommand,
         name='wget_enUS',
         command=['make', 'wget-en-US'],
         descriptionDone='wget en-US',
         env={'EN_US_BINARY_URL': self.enUSBinaryURL},
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName)
        )

    def updateEnUS(self):
        self.addStep(ShellCommand,
         name='make_unpack',
         command=['make', 'unpack'],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName)
        )
        self.addStep(SetProperty,
         command=['make', 'ident'],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName),
         extract_fn=identToProperties()
        )
        self.addStep(ShellCommand,
         name='hg_update_gecko_revision',
         command=['hg', 'update', '-C', '-r', WithProperties('%(gecko_revision)s')],
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.origSrcDir)
        )
        self.addStep(ShellCommand,
         name='hg_update_fennec_revision',
         command=['hg', 'update', '-C', '-r', WithProperties('%(fennec_revision)s')],
         haltOnFailure=True,
         workdir='%s/%s/%s' % (self.baseWorkDir, self.origSrcDir, self.appName)
        )

    def tinderboxPrintRevisions(self):
        self.tinderboxPrint('fennec_revision',WithProperties('%(fennec_revision)s'))
        self.tinderboxPrint('l10n_revision',WithProperties('%(l10n_revision)s'))
        self.tinderboxPrint('gecko_revision',WithProperties('%(gecko_revision)s'))

    def doRepack(self):
        self.addStep(ShellCommand,
         name='repack_locales',
         command=['sh','-c',
                  WithProperties('make installers-%(locale)s LOCALE_MERGEDIR=$PWD/merged')],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName)
        )

    # Upload targets aren't defined in mobile/locales/Makefile,
    # so use MozillaStageUpload for now.
    def addUploadSteps(self, platform):
        self.addStep(MozillaStageUpload,
            name='upload',
            objdir="%s/dist" % (self.origSrcDir),
            username=self.stageUsername,
            milestone=self.baseUploadDir,
            platform=platform,
            remoteHost=self.stageServer,
            remoteBasePath=self.stageBasePath,
            group=self.stageGroup,
            packageGlob=WithProperties('%s' % self.packageGlob),
            sshKey=self.stageSshKey,
            uploadCompleteMar=False,
            uploadLangPacks=False,
            releaseToLatest=self.nightly,
            releaseToDated=False,
            releaseToTinderboxBuilds=True,
            remoteCandidatesPath=self.stageBasePath,
            tinderboxBuildsDir=self.baseUploadDir,
            dependToDated=False,
            workdir='%s/%s/dist' % (self.baseWorkDir, self.origSrcDir)
        )

    def doUpload(self):
        pass

    def preClean(self):
        self.addStep(ShellCommand,
         name='rm_dist',
         command=['sh', '-c',
                  'if [ -d '+self.mozillaSrcDir+'/dist ]; then ' +
                  'rm -rf '+self.mozillaSrcDir+'/dist; ' +
                  'fi'],
         description=['rm', 'dist'],
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )


class MaemoNightlyRepackFactory(MobileNightlyRepackFactory):
    extraConfigureArgs = ['--target=arm-linux-gnueabi']

    def __init__(self, **kwargs):
        # Only for Maemo we upload the 'en-US' binaries under an 'en-US' subdirectory
        assert 'enUSBinaryURL' in kwargs
        assert kwargs['enUSBinaryURL'] is not ''
        kwargs['enUSBinaryURL'] = '%s/en-US' % kwargs['enUSBinaryURL']
        MobileNightlyRepackFactory.__init__(self, **kwargs)
        assert self.configRepoPath

    def getMozconfig(self):
        self.addStep(ShellCommand,
            name='rm_configs',
            command=['rm', '-rf', 'configs'],
            workdir=self.baseWorkDir,
            description=['removing', 'configs'],
            descriptionDone=['remove', 'configs'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='pull_configs',
            command=['hg', 'clone', 'http://%s/%s' % (self.hgHost,
                                              self.configRepoPath),
                     'configs'],
            workdir=self.baseWorkDir,
            timeout=30*60 # 30 minutes
        )
        self.addStep(ShellCommand,
            name='copy_mozconfig',
            command=['cp', self.mozconfig,
                     '%s/.mozconfig' % self.origSrcDir],
            workdir=self.baseWorkDir,
            description=['copying', 'mozconfig'],
            descriptionDone=['copied', 'mozconfig'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='cat_mozconfig',
            command=['cat', '.mozconfig'],
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['cat', 'mozconfig']
        )

    def downloadBuilds(self):
        MobileNightlyRepackFactory.downloadBuilds(self)
        self.addStep(ShellCommand,
         name='wget_deb',
         command=['make', 'wget-deb', 'EN_US_BINARY_URL=%s' % self.enUSBinaryURL,
                  'DEB_BUILD_ARCH=armel'],
         workdir='%s/%s/%s/locales' % (self.baseWorkDir,
                                       self.origSrcDir, self.appName),
         haltOnFailure=True,
         description=['wget','deb'],
        )

    def doRepack(self):
        mergeOptions = ""
        if self.mergeLocales:
            mergeOptions = 'LOCALE_MERGEDIR=$PWD/merged'
        self.addStep(ShellCommand,
         name='repack_debs',
         command=['sh','-c',
                  WithProperties('make installers-%(locale)s deb-%(locale)s ' +
                                 'DEB_BUILD_ARCH=armel ' +
                                 mergeOptions)],
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName),
         haltOnFailure=True,
         description=['repack','deb'],
        )

    def doUpload(self):
        self.addStep(ShellCommand,
         name='copy_deb',
         command=['sh', '-c', WithProperties('if [ -f %(locale)s/*.deb ] ' +
                  '; then cp -r %(locale)s ' + '%s/%s/dist ; fi' %
                  (self.baseWorkDir, self.origSrcDir))],
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName),
        )
        self.addUploadSteps(platform='linux')

class MaemoReleaseRepackFactory(MaemoNightlyRepackFactory):
    def __init__(self, **kwargs):
        assert 'l10nTag' in kwargs
        MaemoNightlyRepackFactory.__init__(self, **kwargs)

    def doUpload(self):
        self.addStep(ShellCommand,
         name='copy_deb',
         command=['sh', '-c', WithProperties('if [ -f %(locale)s/*.deb ] ' +
                  '; then cp -r %(locale)s ' + '%s/%s/dist ; fi' %
                  (self.baseWorkDir, self.origSrcDir))],
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.origSrcDir,
                                       self.appName),
         haltOnFailure=True,
        )
        self.addStep(ShellCommand,
         name='copy_files',
         command=['sh', '-c',
                  WithProperties('cp fennec-*.%(locale)s.linux-gnueabi-arm.tar.bz2 ' +
                                 'install/fennec-*.%(locale)s.langpack.xpi ' +
                                 '%(locale)s/')],
         workdir='%s/%s/dist' % (self.baseWorkDir, self.origSrcDir),
         haltOnFailure=True,
        )
        self.addUploadSteps(platform='linux')

    # Upload targets aren't defined in mobile/locales/Makefile,
    # so use MozillaStageUpload for now.
    def addUploadSteps(self, platform):
        self.addStep(MozillaStageUpload,
            name='upload',
            objdir="%s/dist" % (self.origSrcDir),
            username=self.stageUsername,
            milestone=self.baseUploadDir,
            platform=platform,
            remoteHost=self.stageServer,
            remoteBasePath=self.stageBasePath,
            group=self.stageGroup,
            packageGlob=WithProperties('%s' % self.packageGlob),
            sshKey=self.stageSshKey,
            uploadCompleteMar=False,
            uploadLangPacks=False,
            releaseToLatest=False,
            releaseToDated=False,
            releaseToTinderboxBuilds=False,
            releaseToCandidates=True,
            remoteCandidatesPath=self.stageBasePath,
            tinderboxBuildsDir=self.baseUploadDir,
            dependToDated=False,
            workdir='%s/%s/dist' % (self.baseWorkDir, self.origSrcDir)
        )

class MobileDesktopNightlyRepackFactory(MobileNightlyRepackFactory):
    def doUpload(self):
        self.addUploadSteps(platform=self.platform)


class PartnerRepackFactory(ReleaseFactory):
    def getReleaseTag(self, product, version):
        return product.upper() + '_' + \
               str(version).replace('.','_') + '_' + \
               'RELEASE'

    def __init__(self, productName, version, partnersRepoPath, 
                 stagingServer, stageUsername, stageSshKey, 
                 buildNumber=1, partnersRepoRevision='default', 
                 **kwargs):
        ReleaseFactory.__init__(self, **kwargs)
        self.productName = productName
        self.version = version
        self.buildNumber = buildNumber
        self.partnersRepoPath = partnersRepoPath
        self.partnersRepoRevision = partnersRepoRevision
        self.stagingServer = stagingServer
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey
        self.partnersRepackDir = 'partner-repacks'
        self.candidatesDir = self.getCandidatesDir(productName,
                                                   version,
                                                   buildNumber)
        self.releaseTag = self.getReleaseTag(productName, version)

        self.getPartnerRepackData()
        self.doPartnerRepacks()
        self.uploadPartnerRepacks()

    def getPartnerRepackData(self):
        # We start fresh every time.
        self.addStep(ShellCommand,
            name='rm_partners_repo',
            command=['rm', '-rf', self.partnersRepackDir],
            description=['remove', 'partners', 'repo'],
            workdir='.'
        )
        self.addStep(ShellCommand,
            name='clone_partners_repo',
            command=['hg', 'clone', 
                     'http://%s/%s %s' % (self.hgHost, 
                                          self.partnersRepoPath,
                                          self.partnersRepackDir)
                    ],
            description=['clone', 'partners', 'repo'],
            workdir='.',
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='update_partners_repo',
            command=['hg', 'update', '-r', self.partnersRepoRevision],
            description=['update', 'partners', 'repo'],
            workdir=self.partnersRepackDir,
            haltOnFailure=True            
        )
        self.addStep(ShellCommand,
            name='download_pkg-dmg',
            command=['bash', '-c',
                     'wget http://hg.mozilla.org/%s/raw-file/%s/build/package/mac_osx/pkg-dmg' % (self.repoPath, self.releaseTag)],
            description=['download', 'pkg-dmg'],
            workdir='%s/scripts' % self.partnersRepackDir,
            haltOnFailure=True            
        )
        self.addStep(ShellCommand,
            name='chmod_pkg-dmg',
            command=['chmod', '755', 'pkg-dmg'],
            description=['chmod', 'pkg-dmg'],
            workdir='%s/scripts' % self.partnersRepackDir,
            haltOnFailure=True            
        )
        self.addStep(SetProperty,
            name='set_scriptsdir',
            command=['bash', '-c', 'pwd'],
            property='scriptsdir',
            workdir='%s/scripts' % self.partnersRepackDir,
        )

    def doPartnerRepacks(self):
        self.addStep(RepackPartners,
            name='repack_partner_builds',
            command=['./partner-repacks.py',
                     '--version', str(self.version),
                     '--build-number', str(self.buildNumber),
                     '--pkg-dmg', WithProperties('%(scriptsdir)s/pkg-dmg'),
                     '--staging-server', self.stagingServer,
                    ],
            description=['repacking', 'partner', 'builds'],
            descriptionDone=['repacked', 'partner', 'builds'],
            workdir='%s/scripts' % self.partnersRepackDir,
            haltOnFailure=True
        )            

    def uploadPartnerRepacks(self):
        self.addStep(ShellCommand,
         name='upload_partner_builds',
         command=['rsync', '-av',
                  '-e', 'ssh -oIdentityFile=~/.ssh/%s' % self.stageSshKey,
                  '*',
                  '%s@%s:%s' % (self.stageUsername,
                                self.stagingServer,
                                self.candidatesDir) + \
                  'unsigned/partner-repacks'
                  ],
         workdir='%s/scripts/repacked_builds/%s/build%s' % (self.partnersRepackDir,
                                                            self.version,
                                                            str(self.buildNumber)),
         description=['upload', 'partner', 'builds'],
         haltOnFailure=True
        )
        
