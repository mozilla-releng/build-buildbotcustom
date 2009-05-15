from datetime import datetime
import os.path, re
from time import strftime

from twisted.python import log

from buildbot.process.factory import BuildFactory
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

from buildbotcustom.steps.misc import SetMozillaBuildProperties, \
  TinderboxShellCommand, SendChangeStep, GetBuildID, MozillaClobberer, \
  FindFile, DownloadFile, UnpackFile, SetBuildProperty, GetHgRevision
from buildbotcustom.steps.release import UpdateVerify, L10nVerifyMetaDiff
from buildbotcustom.steps.test import AliveTest, CompareBloatLogs, \
  CompareLeakLogs, Codesighs, GraphServerPost
from buildbotcustom.steps.transfer import MozillaStageUpload
from buildbotcustom.steps.updates import CreateCompleteUpdateSnippet
from buildbotcustom.env import MozillaEnvironments

import buildbotcustom.steps.unittest as unittest_steps

import buildbotcustom.steps.talos as talos_steps

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
         description='clean checkout',
         workdir='.', 
         command=['rm', '-rf', 'build'],
         haltOnFailure=1)
        self.addStep(ShellCommand, 
         description='checkout', 
         workdir='.',
         command=['cvs', '-d', cvsroot, 'co', '-r', automation_tag,
                  '-d', 'build', cvsmodule],
         haltOnFailure=1,
        )
        self.addStep(ShellCommand, 
         description='copy bootstrap.cfg',
         command=['cp', bootstrap_config, 'bootstrap.cfg'],
         haltOnFailure=1,
        )
        self.addStep(ShellCommand, 
         description='echo bootstrap.cfg',
         command=['cat', 'bootstrap.cfg'],
         haltOnFailure=1,
        )
        self.addStep(ShellCommand, 
         description='(re)create logs area',
         command=['bash', '-c', 'mkdir -p ' + logdir], 
         haltOnFailure=1,
        )

        self.addStep(ShellCommand, 
         description='clean logs area',
         command=['bash', '-c', 'rm -rf ' + logdir + '/*.log'], 
         haltOnFailure=1,
        )
        self.addStep(ShellCommand, 
         description='unit tests',
         command=['make', 'test'], 
         haltOnFailure=1,
        )


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
                 **kwargs):
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

        self.repository = self.getRepository(repoPath)
        self.branchName = self.getRepoName(self.repository)

        self.addStep(ShellCommand,
         command=['echo', WithProperties('Building on: %(slavename)s')]
        )
        self.addInitialSteps()

    def addInitialSteps(self):
        self.addStep(ShellCommand,
         command=['rm', '-rf', 'tools'],
         description=['clobber', 'build tools'],
         workdir='.'
        )
        self.addStep(ShellCommand,
         command=['bash', '-c',
          'if [ ! -d tools ]; then hg clone %s; fi' % self.buildToolsRepo],
         description=['clone', 'build tools'],
         workdir='.'
        )
        # We need the basename of the current working dir so we can
        # ignore that dir when purging builds later.
        self.addStep(SetProperty,
         command=['bash', '-c', 'basename "$PWD"'],
         property='builddir',
         workdir='.'
        )

        if self.clobberURL is not None and self.clobberTime is not None:
            self.addStep(MozillaClobberer,
             branch=self.branchName,
             clobber_url=self.clobberURL,
             clobberer_path='tools/clobberer/clobberer.py',
             clobberTime=self.clobberTime
            )

        if self.buildSpace > 0:
            command = ['python', 'tools/buildfarm/maintenance/purge_builds.py',
                 '-s', str(self.buildSpace)]

            for i in self.ignore_dirs:
                command.extend(["-n", i])
            # Ignore the current dir also.
            command.extend(["-n", WithProperties("%(builddir)s")])
            command.append("..")

            self.addStep(ShellCommand,
             command=command,
             description=['cleaning', 'old', 'builds'],
             descriptionDone=['clean', 'old', 'builds'],
             warnOnFailure=True,
             flunkOnFailure=False,
             workdir='.',
             timeout=3600, # One hour, because Windows is slow
            )

    def addPeriodicRebootSteps(self):
        self.addStep(ShellCommand,
         command=['python', 'tools/buildfarm/maintenance/count_and_reboot.py',
                  '-f', '../reboot_count.txt',
                  '-n', str(self.buildsBeforeReboot),
                  '-z'],
         description=['maybe rebooting'],
         warnOnFailure=False,
         flunkOnFailure=False,
         alwaysRun=True,
         workdir='.'
        )

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



class MercurialBuildFactory(MozillaBuildFactory):
    def __init__(self, env, objdir, platform, configRepoPath, configSubDir,
                 profiledBuild, mozconfig, productName=None, buildRevision=None,
                 stageServer=None, stageUsername=None, stageGroup=None,
                 stageSshKey=None, stageBasePath=None, ausBaseUploadDir=None,
                 updatePlatform=None, downloadBaseURL=None, ausUser=None,
                 ausHost=None, nightly=False, leakTest=False, codesighs=True,
                 graphServer=None, graphSelector=None, graphBranch=None,
                 baseName=None, uploadPackages=True, uploadSymbols=True,
                 createSnippet=False, doCleanup=True, packageSDK=False,
                 packageTests=False, mozillaDir=None, **kwargs):
        MozillaBuildFactory.__init__(self, **kwargs)
        self.env = env.copy()
        self.objdir = objdir
        self.platform = platform
        self.configRepoPath = configRepoPath
        self.configSubDir = configSubDir
        self.profiledBuild = profiledBuild
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
        assert self.platform in ('linux', 'linux64', 'win32', 'macosx')

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
        if self.uploadSymbols or self.uploadPackages:
            self.addBuildSymbolsStep()
        if self.uploadSymbols:
            self.addUploadSymbolsStep()
        if self.uploadPackages:
            self.addUploadSteps()
        if self.leakTest:
            self.addLeakTestSteps()
        if self.codesighs:
            self.addCodesighsSteps()
        if self.createSnippet:
            self.addUpdateSteps()
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
             command=['rm', '-rf', 'build'],
             env=self.env,
             workdir='.',
             timeout=60*60 # 1 hour
            )
        self.addStep(ShellCommand,
         command="rm -rf %s/dist/%s-* %s/dist/install/sea/*.exe " %
                  (self.mozillaObjdir, self.productName, self.mozillaObjdir),
         env=self.env,
         description=['deleting', 'old', 'package'],
         descriptionDone=['delete', 'old', 'package']
        )
        if self.nightly:
            self.addStep(ShellCommand,
             command="find 20* -maxdepth 2 -mtime +7 -exec rm -rf {} \;",
             env=self.env,
             workdir='.',
             description=['cleanup', 'old', 'symbols'],
             flunkOnFailure=False
            )

    def addSourceSteps(self):
        self.addStep(Mercurial,
         mode='update',
         baseURL='http://%s/' % self.hgHost,
         defaultBranch=self.repoPath,
         timeout=60*60, # 1 hour
        )
        if self.buildRevision:
            self.addStep(ShellCommand,
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             haltOnFailure=True
            )
            self.addStep(SetProperty,
             command=['hg', 'identify', '-i'],
             property='got_revision'
            )
        changesetLink = '<a href=http://%s/%s/index.cgi/rev' % (self.hgHost,
                                                                self.repoPath)
        changesetLink += '/%(got_revision)s title="Built from revision %(got_revision)s">rev:%(got_revision)s</a>'
        self.addStep(ShellCommand,
         command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)]
        )

    def addConfigSteps(self):
        assert configRepoPath is not None
        assert configSubDir is not None
        assert self.mozconfig is not None
        configRepo = self.getRepository(self.configRepoPath)

        self.mozconfig = 'configs/%s/%s/mozconfig' % (self.configSubDir,
                                                      self.mozconfig)
        self.addStep(ShellCommand,
         command=['rm', '-rf', 'configs'],
         description=['removing', 'configs'],
         descriptionDone=['remove', 'configs'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['hg', 'clone', self.configRepo, 'configs'],
         description=['checking', 'out', 'configs'],
         descriptionDone=['checkout', 'configs'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         # cp configs/mozilla2/$platform/$repo/$type/mozconfig .mozconfig
         command=['cp', self.mozconfig, '.mozconfig'],
         description=['copying', 'mozconfig'],
         descriptionDone=['copy', 'mozconfig'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['cat', '.mozconfig'],
        )

    def addDoBuildSteps(self):
        buildcmd = 'build'
        if self.profiledBuild:
            buildcmd = 'profiledbuild'
        self.addStep(ShellCommand,
         command=['make', '-f', 'client.mk', buildcmd],
         description=['compile'],
         env=self.env,
         haltOnFailure=True,
         timeout=5400 # 90 minutes, because windows PGO builds take a long time
        )

    def addLeakTestSteps(self):
        # we want the same thing run a few times here, with different
        # extraArgs
        for args in [['-register'], ['-CreateProfile', 'default'],
                     ['-P', 'default']]:
            self.addStep(AliveTest,
                env=self.env,
                workdir='build/%s/_leaktest' % self.mozillaObjdir,
                extraArgs=args,
                warnOnFailure=True,
                haltOnFailure=True
            )
        # we only want this variable for this test - this sucks
        bloatEnv = self.env.copy()
        bloatEnv['XPCOM_MEM_BLOAT_LOG'] = '1' 
        self.addStep(AliveTest,
         env=bloatEnv,
         workdir='build/%s/_leaktest' % self.mozillaObjdir,
         logfile='bloat.log',
         warnOnFailure=True,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         env=self.env,
         workdir='.',
         command=['wget', '-O', 'bloat.log.old',
                  'http://%s/pub/mozilla.org/%s/%s/bloat.log' % \
                    (self.stageServer, self.productName, self.logUploadDir)]
        )
        self.addStep(ShellCommand,
         env=self.env,
         command=['mv', '%s/_leaktest/bloat.log' % self.mozillaObjdir,
                  '../bloat.log'],
        )
        self.addStep(ShellCommand,
         env=self.env,
         command=['scp', '-o', 'User=%s' % self.stageUsername,
                  '-o', 'IdentityFile=~/.ssh/%s' % self.stageSshKey,
                  '../bloat.log',
                  '%s:%s/%s' % (self.stageServer, self.stageBasePath,
                                self.logUploadDir)]
        )
        self.addStep(CompareBloatLogs,
         bloatLog='bloat.log',
         env=self.env,
         workdir='.',
         mozillaDir=self.mozillaDir,
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
        self.addStep(AliveTest,
         env=self.env,
         workdir='build/%s/_leaktest' % self.mozillaObjdir,
         extraArgs=['--trace-malloc', 'malloc.log',
                    '--shutdown-leaks=sdleak.log'],
         timeout=3600, # 1 hour, because this takes a long time on win32
         warnOnFailure=True,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         env=self.env,
         workdir='.',
         command=['wget', '-O', 'malloc.log.old',
                  'http://%s/pub/mozilla.org/%s/%s/malloc.log' % \
                    (self.stageServer, self.productName, self.logUploadDir)]
        )
        self.addStep(ShellCommand,
         env=self.env,
         workdir='.',
         command=['wget', '-O', 'sdleak.tree.old',
                  'http://%s/pub/mozilla.org/%s/%s/sdleak.tree' % \
                    (self.stageServer, self.productName, self.logUploadDir)]
        )
        self.addStep(ShellCommand,
         env=self.env,
         command=['mv',
                  '%s/_leaktest/malloc.log' % self.mozillaObjdir,
                  '../malloc.log'],
        )
        self.addStep(ShellCommand,
         env=self.env,
         command=['mv',
                  '%s/_leaktest/sdleak.log' % self.mozillaObjdir,
                  '../sdleak.log'],
        )
        self.addStep(CompareLeakLogs,
         mallocLog='../malloc.log',
         platform=self.platform,
         env=self.env,
         objdir=self.mozillaObjdir,
         testname='current',
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
         mallocLog='../malloc.log.old',
         platform=self.platform,
         env=self.env,
         objdir=self.mozillaObjdir,
         testname='previous'
        )
        self.addStep(ShellCommand,
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
             env=self.env,
             workdir='.',
             command=['mv', 'sdleak.tree', 'sdleak.tree.raw']
            )
            self.addStep(ShellCommand,
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
         env=self.env,
         command=['scp', '-o', 'User=%s' % self.stageUsername,
                  '-o', 'IdentityFile=~/.ssh/%s' % self.stageSshKey,
                  '../malloc.log', '../sdleak.tree',
                  '%s:%s/%s' % (self.stageServer, self.stageBasePath,
                                self.logUploadDir)]
        )
        self.addStep(ShellCommand,
         env=self.env,
         workdir='.',
         command=['perl', 'build%s/tools/trace-malloc/diffbloatdump.pl' % self.mozillaDir,
                  '--depth=15', 'sdleak.tree.old', 'sdleak.tree']
        )

    def addUploadSteps(self, pkgArgs=None):
        pkgArgs = pkgArgs or []
        if self.packageSDK:
            self.addStep(ShellCommand,
             command=['make', '-f', 'client.mk', 'sdk'],
             env=self.env,
             workdir='build/',
             haltOnFailure=True
            )
        if self.packageTests:
            self.addStep(ShellCommand,
             command=['make', 'package-tests'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True,
            )
        self.addStep(ShellCommand,
         command=['make', 'package'] + pkgArgs,
         env=self.env,
         workdir='build/%s' % self.objdir,
         haltOnFailure=True
        )
        if self.platform.startswith("win32"):
         self.addStep(ShellCommand,
             command=['make', 'installer'] + pkgArgs,
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True
         )
        if self.createSnippet:
         self.addStep(ShellCommand,
             command=['make', '-C',
                      '%s/tools/update-packaging' % self.mozillaObjdir],
             env=self.env,
             haltOnFailure=True
         )
        if self.productName == 'xulrunner':
            self.addStep(GetBuildID,
             objdir=self.objdir,
             inifile='platform.ini',
             section='Build',
            )
        else:
            self.addStep(SetMozillaBuildProperties,
             objdir='build/%s' % self.mozillaObjdir
            )

        # Call out to a subclass to do the actual uploading
        self.doUpload()
        
    def addCodesighsSteps(self):
        self.addStep(ShellCommand,
         command=['make'],
         workdir='build/%s/tools/codesighs' % self.mozillaObjdir
        )
        self.addStep(ShellCommand,
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
         objdir=codesighsObjdir,
         platform=self.platform,
         workdir='build%s' % self.mozillaDir,
         env=self.env
        )
        if self.graphServer:
            self.addStep(GraphServerPost,
             server=self.graphServer,
             selector=self.graphSelector,
             branch=self.graphBranch,
             resultsname=self.baseName
            )
        self.addStep(ShellCommand,
         command=['cat', '../codesize-auto-diff.log'],
         workdir='build%s' % self.mozillaDir
        )
        self.addStep(ShellCommand,
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
         baseurl='%s/nightly' % self.downloadBaseURL
        )
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
         workdir='build/%s' % self.mozillaObjdir,
         description=['upload', 'complete', 'snippet'],
         haltOnFailure=True
        )

    def addBuildSymbolsStep(self):
        self.addStep(ShellCommand,
         command=['make', 'buildsymbols'],
         env=self.env,
         workdir='build/%s' % self.objdir,
         haltOnFailure=True
        )

    def addUploadSymbolsStep(self):
        self.addStep(ShellCommand,
         command=['make', 'uploadsymbols'],
         env=self.env,
         workdir='build/%s' % self.objdir,
         haltOnFailure=True
        )

    def addPostBuildCleanupSteps(self):
        if self.nightly:
            self.addStep(ShellCommand,
             command=['rm', '-rf', 'build'],
             env=self.env,
             workdir='.',
             timeout=5400 # 1.5 hours
            )

class CCMercurialBuildFactory(MercurialBuildFactory):
    def __init__(self, mozRepoPath, **kwargs):
        self.mozRepoPath = mozRepoPath
        MercurialBuildFactory.__init__(self, mozillaDir='mozilla', **kwargs)

    def addSourceSteps(self):
        self.addStep(Mercurial, mode='update',
         baseURL='http://%s/' % self.hgHost,
         defaultBranch=self.repoPath,
         alwaysUseLatest=True,
         timeout=60*60 # 1 hour
        )

        if self.buildRevision:
            self.addStep(ShellCommand,
             command=['hg', 'up', '-C', '-r', self.buildRevision],
             haltOnFailure=True
            )
            self.addStep(SetProperty,
             command=['hg', 'identify', '-i'],
             property='got_revision'
            )
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost, self.repoPath)
        changesetLink += '/%(got_revision)s title="Built from revision %(got_revision)s">rev:%(got_revision)s</a>'
        self.addStep(ShellCommand,
         command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)]
        )
        self.addStep(ShellCommand,
         name="client.py checkout",
         command=['python', 'client.py', 'checkout']
        )

        self.addStep(SetProperty,
         command=['hg', 'identify', '-i'],
         workdir='build/mozilla',
         property='hg_revision'
        )
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost, self.mozRepoPath)
        changesetLink += '/%(hg_revision)s title="Built from Mozilla revision %(hg_revision)s">moz:%(hg_revision)s</a>'
        self.addStep(ShellCommand,
         command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)]
        )



class NightlyBuildFactory(MercurialBuildFactory):
    def __init__(self, talosMasters=None, unittestMasters=None, **kwargs):
        if talosMasters is None:
            self.talosMasters = []
        else:
            self.talosMasters = talosMasters

        if unittestMasters is None:
            self.unittestMasters = []
        else:
            self.unittestMasters = unittestMasters
        MercurialBuildFactory.__init__(self, **kwargs)

    def doUpload(self):
        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_TO_TEMP': '1'})
        if self.stageSshKey:
            uploadEnv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        # Always upload builds to the dated tinderbox builds directories
        postUploadCmd =  ['post_upload.py']
        postUploadCmd += ['--tinderbox-builds-dir %s-%s' % (self.branchName,
                                                            self.platform),
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
            )
        else:
            self.addStep(SetProperty,
             command=['make', 'upload'],
             env=uploadEnv,
             workdir='build/%s' % self.objdir,
             extract_fn = get_url,
            )

        talosBranch = "%s-%s" % (self.branchName, self.platform)
        for master, warn in self.talosMasters:
            self.addStep(SendChangeStep(
             warnOnFailure=warn,
             master=master,
             branch=talosBranch,
             files=[WithProperties('%(packageUrl)s')],
             user="sendchange")
            )
        unittestBranch = "%s-%s-unittest" % (self.branchName, self.platform)
        for master, warn in self.unittestMasters:
            self.addStep(SendChangeStep(
             warnOnFailure=warn,
             master=master,
             branch=unittestBranch,
             files=[WithProperties('%(packageUrl)s')],
             user="sendchange-unittest")
            )

class CCNightlyBuildFactory(CCMercurialBuildFactory, NightlyBuildFactory):
    def __init__(self, mozRepoPath, **kwargs):
        self.mozRepoPath = mozRepoPath
        NightlyBuildFactory.__init__(self, mozillaDir='mozilla', **kwargs)



class ReleaseBuildFactory(MercurialBuildFactory):
    def __init__(self, version, buildNumber, brandName=None, **kwargs):
        self.version = version
        self.buildNumber = buildNumber

        if brandName:
            self.brandName = brandName
        else:
            self.brandName = kwargs['productName'].capitalize()
        # Make sure MOZ_PKG_PRETTYNAMES is on and override MOZ_PKG_VERSION
        # The latter is only strictly necessary for RCs.
        kwargs['env']['MOZ_PKG_PRETTYNAMES'] = '1'
        kwargs['env']['MOZ_PKG_VERSION'] = version
        MercurialBuildFactory.__init__(self, **kwargs)

    def doUpload(self):
        # Make sure the complete MAR has been generated
        self.addStep(ShellCommand,
            command=['make', '-C',
                     '%s/tools/update-packaging' % self.mozillaObjdir],
            env=self.env,
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
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
        self.addStep(ShellCommand,
         command=['make', 'upload'],
         env=uploadEnv,
         workdir='build/%s' % self.objdir
        )


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

    def __init__(self, project, appName, l10nRepoPath, stageServer,
                 stageUsername, stageSshKey=None, baseWorkDir='build',
                 **kwargs):
        MozillaBuildFactory.__init__(self, **kwargs)

        self.project = project
        self.appName = appName
        self.l10nRepoPath = l10nRepoPath
        self.stageServer = stageServer
        self.stageUsername = stageUsername
        self.stageSshKey = stageSshKey

        # Needed for scratchbox
        self.baseWorkDir = baseWorkDir

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

        self.addStep(ShellCommand,
         command=['sh', '-c',
                  'if [ -d '+self.branchName+'/dist/upload ]; then ' +
                  'rm -rf '+self.branchName+'/dist/upload; ' +
                  'fi'],
         description="rm dist/upload",
         workdir=self.baseWorkDir,
         haltOnFailure=True
        )

        self.addStep(ShellCommand,
         command=['sh', '-c', 'mkdir -p %s' % l10nRepoPath],
         descriptionDone='mkdir '+ l10nRepoPath,
         workdir=self.baseWorkDir,
         flunkOnFailure=False
        )

        # call out to overridable functions
        self.getSources()
        self.updateSources()
        self.getMozconfig()

        self.addStep(ShellCommand,
         command=['bash', '-c', 'autoconf-2.13'],
         haltOnFailure=True,
         descriptionDone=['autoconf'],
         workdir='%s/%s' % (self.baseWorkDir, self.branchName)
        )
        self.addStep(ShellCommand,
         command=['bash', '-c', 'autoconf-2.13'],
         haltOnFailure=True,
         descriptionDone=['autoconf js/src'],
         workdir='%s/%s/js/src' % (self.baseWorkDir, self.branchName)
        )
        self.addStep(ShellCommand,
         command=['sh', '--',
                  './configure', '--enable-application=%s' % self.appName,
                  '--with-l10n-base=../%s' % l10nRepoPath ] +
                  self.extraConfigureArgs,
         description='configure',
         descriptionDone='configure done',
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.branchName)
        )
        for dir in ('nsprpub', 'config'):
            self.addStep(ShellCommand,
             command=['make'],
             workdir='%s/%s/%s' % (self.baseWorkDir, self.branchName, dir),
             description=['make', dir],
             haltOnFailure=True
            )

        self.downloadBuilds()
        self.updateEnUS()
        self.doRepack()
        self.doUpload()

    def doUpload(self):
        self.addStep(ShellCommand,
         command=['make', WithProperties('l10n-upload-%(locale)s')],
         env=self.uploadEnv,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.branchName,
                                       self.appName),
         flunkOnFailure=True
        )

    def getSources(self):
        self.addStep(ShellCommand,
         command=['sh', '-c',
          WithProperties('if [ -d '+self.branchName+' ]; then ' +
                         'hg -R '+self.branchName+' pull -r default ; ' +
                         'else ' +
                         'hg clone ' +
                         'http://'+self.hgHost+'/'+self.repoPath+' ; ' +
                         'fi ' +
                         '&& hg -R '+self.branchName+' update -r %(en_revision)s')],
         descriptionDone="en-US source",
         workdir=self.baseWorkDir,
         timeout=30*60 # 30 minutes
        )
        self.addStep(ShellCommand,
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
         workdir='%s/%s' % (self.baseWorkDir, self.l10nRepoPath)
        )

    def updateEnUS(self):
        '''Update the en-US source files to the revision used by
        the repackaged build.

        This is implemented in the subclasses.
        '''
        pass


class NightlyRepackFactory(BaseRepackFactory):
    def __init__(self, enUSBinaryURL, **kwargs):
        self.enUSBinaryURL = enUSBinaryURL
        # Unfortunately, we can't call BaseRepackFactory.__init__() before this
        # because it needs self.postUploadCmd set
        assert 'project' in kwargs
        assert 'repoPath' in kwargs
        uploadDir = '%s-l10n' % self.getRepoName(kwargs['repoPath'])
        self.postUploadCmd = 'post_upload.py ' + \
                             '-p %s ' % kwargs['project'] + \
                             '-b %s ' % uploadDir + \
                             '--release-to-latest'

        self.env = {}

        BaseRepackFactory.__init__(self, **kwargs)

    def updateSources(self):
        self.addStep(ShellCommand,
         command=['hg', 'up', '-C', '-r', 'default'],
         description='update workdir',
         workdir=WithProperties('build/' + self.l10nRepoPath + '/%(locale)s'),
         haltOnFailure=True
        )

    def getMozconfig(self):
        pass

    def downloadBuilds(self):
        self.addStep(ShellCommand,
         command=['make', 'wget-en-US'],
         descriptionDone='wget en-US',
         env={'EN_US_BINARY_URL': self.enUSBinaryURL},
         haltOnFailure=True,
         workdir='build/'+self.branchName+'/'+self.appName+'/locales'
        )

    def updateEnUS(self):
        '''Update en-US to the source stamp we get from make ident.

        Requires that we run make unpack first.
        '''
        self.addStep(ShellCommand,
                     command=['make', 'unpack'],
                     descriptionDone='unpacked en-US',
                     haltOnFailure=True,
                     workdir='build/'+self.branchName+'/'+self.appName+
                     '/locales'
                     )
        self.addStep(SetProperty,
                     command=['make', 'ident'],
                     haltOnFailure=True,
                     workdir='build/'+self.branchName+'/'+self.appName+
                     '/locales',
                     extract_fn=identToProperties('fx_revision')
                     )
        self.addStep(ShellCommand,
                     command=['hg', 'update', '-r',
                              WithProperties('%(fx_revision)s')],
                     haltOnFailure=True,
                     workdir='build/' + self.branchName)

    def doRepack(self):
        self.addStep(ShellCommand,
         command=['make', WithProperties('installers-%(locale)s')],
         haltOnFailure=True,
         workdir='build/'+self.branchName+'/'+self.appName+'/locales'
        )



class ReleaseFactory(MozillaBuildFactory):
    def getCandidatesDir(self, product, version, buildNumber):
        return '/home/ftp/pub/' + product + '/nightly/' + str(version) + \
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
                 buildRevision, version, buildNumber, **kwargs):
        self.configRepoPath = configRepoPath
        self.configSubDir = configSubDir
        self.platform = platform
        self.buildRevision = buildRevision
        self.version = version
        self.buildNumber = buildNumber
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
         command=['hg', 'up', '-C', '-r', self.buildRevision],
         workdir='build/'+self.branchName,
         description=['update %s' % self.branchName,
                      'to %s' % self.buildRevision],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['hg', 'up', '-C', '-r', self.buildRevision],
         workdir=WithProperties('build/' + self.l10nRepoPath + '/%(locale)s'),
         description=['update to', self.buildRevision]
        )

    def getMozconfig(self):
        self.addStep(ShellCommand,
         command=['rm', '-rf', 'configs'],
         description=['remove', 'configs'],
         workdir='build/'+self.branchName,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['hg', 'clone', self.configRepo, 'configs'],
         description=['checkout', 'configs'],
         workdir='build/'+self.branchName,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         # cp configs/mozilla2/$platform/$branchame/$type/mozconfig .mozconfig
         command=['cp', self.mozconfig, '.mozconfig'],
         description=['copy mozconfig'],
         workdir='build/'+self.branchName,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['cat', '.mozconfig'],
         workdir='build/'+self.branchName
        )

    def downloadBuilds(self):
        # We need to know the absolute path to the input builds when we repack,
        # so we need retrieve at run-time as a build property
        self.addStep(SetProperty,
         command=['bash', '-c', 'pwd'],
         property='srcdir',
         workdir='build/'+self.branchName
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
            builds[filename] = '%s %s.dmg' % (self.project.capitalize(),
                                              longVersion)
            self.env['ZIP_IN'] = WithProperties('%(srcdir)s/' + filename)
        elif self.platform.startswith('win32'):
            platformDir = 'unsigned/win32'
            filename = '%s.zip' % self.project
            instname = '%s.exe' % self.project
            builds[filename] = '%s-%s.zip' % (self.project, self.version)
            builds[instname] = '%s Setup %s.exe' % (self.project.capitalize(),
                                                    longVersion)
            self.env['ZIP_IN'] = WithProperties('%(srcdir)s/' + filename)
            self.env['WIN32_INSTALLER_IN'] = \
              WithProperties('%(srcdir)s/' + instname)
        else:
            raise "Unsupported platform"

        for name in builds:
            self.addStep(ShellCommand,
             command=['wget', '-O', name, '--no-check-certificate',
                      '%s/%s/en-US/%s' % (candidatesDir, platformDir,
                                          builds[name])],
             workdir='build/'+self.branchName,
             haltOnFailure=True
            )
        
    def doRepack(self):
        # Because we're generating updates we need to build the libmar tools
        for dir in ('nsprpub', 'config', 'modules/libmar'):
            self.addStep(ShellCommand,
             command=['make'],
             workdir='build/'+self.branchName+'/'+dir,
             description=['make ' + dir],
             haltOnFailure=True
            )

        self.addStep(ShellCommand,
         command=['make', WithProperties('installers-%(locale)s')],
         env=self.env,
         haltOnFailure=True,
         workdir='build/'+self.branchName+'/'+self.appName+'/locales'
        )

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
             command=['bash', '-c', command],
             description=['recreate', repoName],
             timeout=30*60 # 30 minutes
            )



class ReleaseTaggingFactory(ReleaseFactory):
    def __init__(self, repositories, productName, appName, version, appVersion,
                 milestone, baseTag, buildNumber, hgUsername, hgSshKey=None,
                 buildSpace=1.5, **kwargs):
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
               'browser/app/module.ver', 'config/milestone.txt']
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
                  '. You must provide a relbranchOverride when buildNumber > 2'

        # now, down to work
        buildTag = '%s_BUILD%s' % (baseTag, str(buildNumber))
        releaseTag = '%s_RELEASE' % baseTag

        # generate the release branch name, which is based on the
        # version and the current date.
        # looks like: GECKO191_20080728_RELBRANCH
        # This can be overridden per-repository. This case is handled
        # in the loop below
        relbranchName = 'GECKO%s_%s_RELBRANCH' % (
          milestone.replace('.', ''), datetime.now().strftime('%Y%m%d'))
                
        for repoPath in sorted(repositories.keys()):
            repoName = self.getRepoName(repoPath)
            repo = self.getRepository(repoPath)
            pushRepo = self.getRepository(repoPath, push=True)

            sshKeyOption = self.getSshKeyOption(hgSshKey)

            repoRevision = repositories[repoPath]['revision']
            bumpFiles = repositories[repoPath]['bumpFiles']

            relbranchOverride = False
            if repositories[repoPath]['relbranchOverride']:
                relbranchOverride = True
                relbranchName = repositories[repoPath]['relbranchOverride']

            # For l10n we never bump any files, so this will never get
            # overridden. For source repos, we will do a version bump in build1
            # which we commit, and set this property again, so we tag
            # the right revision. For build2, we don't version bump, and this
            # will not get overridden
            self.addStep(SetProperty,
             command=['echo', repoRevision],
             property='%s-revision' % repoName,
             workdir='.',
             haltOnFailure=True
            )
            # 'hg clone -r' breaks in the respin case because the cloned
            # repository will not have ANY changesets from the release branch
            # and 'hg up -C' will fail
            self.addStep(ShellCommand,
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
                 command=['hg', 'up', '-r',
                          WithProperties('%s', '%s-revision' % repoName)],
                 workdir=repoName,
                 description=['update', repoName],
                 haltOnFailure=True
                )
                self.addStep(ShellCommand,
                 command=['hg', 'branch', relbranchName],
                 workdir=repoName,
                 description=['branch %s' % repoName],
                 haltOnFailure=True
                )
            # if buildNumber > 1 we need to switch to it with 'hg up -C'
            else:
                self.addStep(ShellCommand,
                 command=['hg', 'up', '-C', relbranchName],
                 workdir=repoName,
                 description=['switch to', relbranchName],
                 haltOnFailure=True
                )
            # we don't need to do any version bumping if this is a respin
            if buildNumber == 1 and len(bumpFiles) > 0:
                command = ['perl', 'tools/release/version-bump.pl',
                           '-w', repoName, '-t', releaseTag, '-a', appName,
                           '-v', appVersion, '-m', milestone]
                command.extend(bumpFiles)
                self.addStep(ShellCommand,
                 command=command,
                 workdir='.',
                 description=['bump %s' % repoName],
                 haltOnFailure=True
                )
                self.addStep(ShellCommand,
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
                 command=['hg', 'commit', '-u', hgUsername, '-m',
                          'Automated checkin: version bump remove "pre" ' + \
                          ' from version number for ' + productName + ' ' + \
                          version + ' release on ' + relbranchName + ' ' + \
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
            for tag in (buildTag, releaseTag):
                self.addStep(ShellCommand,
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
             command=['hg', 'out', '-e',
                      'ssh -l %s %s' % (hgUsername, sshKeyOption),
                      pushRepo],
             workdir=repoName,
             description=['hg out', repoName]
            )
            self.addStep(ShellCommand,
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
        sourceTarball = 'source/%s-%s-source.tar.bz2' % (productName,
                                                         version)
        # '-c' is for "release to candidates dir"
        postUploadCmd = 'python ~/bin/post_upload.py -p %s -v %s -n %s -c' % \
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
        # This will get us to the version we're building the release with
        self.addStep(ShellCommand,
         command=['hg', 'up', '-C', '-r', releaseTag],
         workdir=self.branchName,
         description=['update to', releaseTag],
         haltOnFailure=True
        )
        # ...And this will get us the tags so people can do things like
        # 'hg up -r FIREFOX_3_1b1_RELEASE' with the bundle
        self.addStep(ShellCommand,
         command=['hg', 'up'],
         workdir=self.branchName,
         description=['update to', 'include tag revs'],
         haltOnFailure=True
        )
        self.addStep(SetProperty,
         command=['hg', 'identify', '-i'],
         property='revision',
         workdir=self.branchName,
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['hg', '-R', self.branchName, 'bundle', '--base', 'null',
                  '-r', WithProperties('%(revision)s'),
                  bundleFile],
         workdir='.',
         description=['create bundle'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['rm', '-rf', '.hg'],
         workdir=self.branchName,
         description=['delete metadata'],
         haltOnFailure=True
        )
        for dir in autoconfDirs:
            self.addStep(ShellCommand,
             command=['autoconf-2.13'],
             workdir='%s/%s' % (self.branchName, dir),
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         command=['tar', '-cjf', sourceTarball, self.branchName],
         workdir='.',
         description=['create tarball'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['python', '%s/build/upload.py' % self.branchName,
                  '--base-path', '.',
                  bundleFile, sourceTarball],
         workdir='.',
         env=uploadEnv,
         description=['upload files'],
        )



class ReleaseUpdatesFactory(ReleaseFactory):
    def __init__(self, cvsroot, patcherToolsTag, patcherConfig, baseTag,
                 appName, productName, version, appVersion, oldVersion,
                 buildNumber, ftpServer, bouncerServer, stagingServer,
                 useBetaChannel, stageUsername, stageSshKey, ausUser, ausHost,
                 commitPatcherConfig=True, buildSpace=13, **kwargs):
        """cvsroot: The CVSROOT to use when pulling patcher, patcher-configs,
                    Bootstrap/Util.pm, and MozBuild. It is also used when
                    commiting the version-bumped patcher config so it must have
                    write permission to the repository if commitPatcherConfig
                    is True.
           patcherToolsTag: A tag that has been applied to all of:
                              sourceRepo, buildTools, patcher,
                              MozBuild, Bootstrap.
                            This version of all of the above tools will be
                            used - NOT tip.
           patcherConfig: The filename of the patcher config file to bump,
                          and pass to patcher.
           commitPatcherConfig: This flag simply controls whether or not
                                the bumped patcher config file will be
                                commited to the CVS repository.
        """
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)

        patcherConfigFile = 'patcher-configs/%s' % patcherConfig
        shippedLocales = self.getShippedLocales(self.repository, baseTag,
                                                appName)
        candidatesDir = self.getCandidatesDir(productName, version, buildNumber)
        updateDir = 'build/temp/%s/%s-%s' % (productName, oldVersion, version)
        marDir = '%s/ftp/%s/nightly/%s-candidates/build%s' % \
          (updateDir, productName, version, buildNumber)

        # If useBetaChannel is False the unnamed snippet type will be
        # 'beta' channel snippets (and 'release' if we're into stable releases).
        # If useBetaChannel is True the unnamed type will be 'release'
        # channel snippets
        snippetTypes = ['', 'test']
        if useBetaChannel:
            snippetTypes.append('beta')

        self.addStep(ShellCommand,
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d', 'build', 'mozilla/tools/patcher'],
         description=['checkout', 'patcher'],
         workdir='.',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d', 'MozBuild',
                  'mozilla/tools/release/MozBuild'],
         description=['checkout', 'MozBuild'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d' 'Bootstrap',
                  'mozilla/tools/release/Bootstrap/Util.pm'],
         description=['checkout', 'Bootstrap/Util.pm'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['cvs', '-d', cvsroot, 'co', '-d' 'patcher-configs',
                  'mozilla/tools/patcher-configs'],
         description=['checkout', 'patcher-configs'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['hg', 'up', '-r', patcherToolsTag],
         description=['update', 'build tools to', patcherToolsTag],
         workdir='tools',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['wget', '-O', 'shipped-locales', shippedLocales],
         description=['get', 'shipped-locales'],
         haltOnFailure=True
        )

        bumpCommand = ['perl', '../tools/release/patcher-config-bump.pl',
                       '-p', productName, '-v', version, '-a', appVersion,
                       '-o', oldVersion, '-b', str(buildNumber),
                       '-c', patcherConfigFile, '-t', stagingServer,
                       '-f', ftpServer, '-d', bouncerServer,
                       '-l', 'shipped-locales']
        if useBetaChannel:
            bumpCommand.append('-u')
        self.addStep(ShellCommand,
         command=bumpCommand,
         description=['bump', patcherConfig],
         haltOnFailure=True
        )
        self.addStep(TinderboxShellCommand,
         command=['cvs', 'diff', '-u', patcherConfigFile],
         description=['diff', patcherConfig],
         ignoreCodes=[1]
        )
        if commitPatcherConfig:
            self.addStep(ShellCommand,
             command=['cvs', 'commit', '-m',
                      'Automated configuration bump: ' + \
                      '%s, from %s to %s build %s' % \
                        (patcherConfig, oldVersion, version, buildNumber)
                     ],
             workdir='build/patcher-configs',
             description=['commit', patcherConfig],
             haltOnFailure=True
            )
        self.addStep(ShellCommand,
         command=['perl', 'patcher2.pl', '--build-tools-hg', 
                  '--tools-revision=%s' % patcherToolsTag,
                  '--app=%s' % productName,
                  '--config=%s' % patcherConfigFile],
         description=['patcher:', 'build tools'],
         env={'HGROOT': self.repository},
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['perl', 'patcher2.pl', '--download',
                  '--app=%s' % productName,
                  '--config=%s' % patcherConfigFile],
         description=['patcher:', 'download builds'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['perl', 'patcher2.pl', '--create-patches',
                  '--partial-patchlist-file=patchlist.cfg',
                  '--app=%s' % productName,
                  '--config=%s' % patcherConfigFile],
         description=['patcher:', 'create patches'],
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['find', marDir, '-type', 'f',
                  '-exec', 'chmod', '644', '{}', ';'],
         workdir='.',
         description=['chmod 644', 'partial mar files']
        )
        self.addStep(ShellCommand,
         command=['find', marDir, '-type', 'd',
                  '-exec', 'chmod', '755', '{}', ';'],
         workdir='.',
         description=['chmod 755', 'partial mar dirs']
        )
        self.addStep(ShellCommand,
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
            remoteDir = '%s-%s-%s' % (date, productName.title(), version)
            if type != '':
                localDir = localDir + '.%s' % type
                remoteDir = remoteDir + '-%s' % type
            snippetDir = '/opt/aus2/snippets/staging/%s' % remoteDir

            self.addStep(ShellCommand,
             command=['rsync', '-av', localDir + '/',
                      '%s@%s:%s' % (ausUser, ausHost, snippetDir)],
             workdir=updateDir,
             description=['upload', '%s snippets' % type],
             haltOnFailure=True
            )

            # We only push test channel snippets from automation.
            if type == 'test':
                self.addStep(ShellCommand,
                 command=['ssh', '-l', ausUser, ausHost,
                          '~/bin/backupsnip %s' % remoteDir],
                 timeout=7200, # 2 hours
                 description=['backupsnip'],
                 haltOnFailure=True
                )
                self.addStep(ShellCommand,
                 command=['ssh', '-l', ausUser, ausHost,
                          '~/bin/pushsnip %s' % remoteDir],
                 timeout=3600, # 1 hour
                 description=['pushsnip'],
                 haltOnFailure=True
                )
                # Wait for timeout on AUS's NFS caching to expire before
                # attempting to test newly-pushed snippets
                self.addStep(ShellCommand,
                 command=['sleep','360'],
                 description=['wait for live snippets']
                )



class UpdateVerifyFactory(ReleaseFactory):
    def __init__(self, cvsroot, patcherToolsTag, hgUsername, baseTag, appName,
                 platform, productName, oldVersion, oldBuildNumber, version,
                 buildNumber, ausServerUrl, stagingServer, verifyConfig,
                 oldAppVersion=None, appVersion=None, hgSshKey=None,
                 buildSpace=.3, **kwargs):
        ReleaseFactory.__init__(self, buildSpace=buildSpace, **kwargs)

        if not oldAppVersion:
            oldAppVersion = oldVersion
        if not appVersion:
            appVersion = version

        oldLongVersion = self.makeLongVersion(oldVersion)
        longVersion = self.makeLongVersion(version)
        # Unfortunately we can't use the getCandidatesDir() function here
        # because that returns it as a file path on the server and we need
        # an http:// compatible path
        oldCandidatesDir = \
          '/pub/mozilla.org/%s/nightly/%s-candidates/build%s' % \
            (productName, oldVersion, oldBuildNumber)

        verifyConfigPath = 'release/updates/%s' % verifyConfig
        shippedLocales = self.getShippedLocales(self.repository, baseTag,
                                                appName)
        pushRepo = self.getRepository(self.buildToolsRepoPath, push=True)
        sshKeyOption = self.getSshKeyOption(hgSshKey)

        self.addStep(ShellCommand,
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d', 'MozBuild',
                  'mozilla/tools/release/MozBuild'],
         description=['checkout', 'MozBuild'],
         workdir='tools',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['cvs', '-d', cvsroot, 'co', '-r', patcherToolsTag,
                  '-d', 'Bootstrap',
                  'mozilla/tools/release/Bootstrap/Util.pm'],
         description=['checkout', 'Bootstrap/Util.pm'],
         workdir='tools',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['wget', '-O', 'shipped-locales', shippedLocales],
         description=['get', 'shipped-locales'],
         workdir='tools',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['perl', 'release/update-verify-bump.pl',
                  '-o', platform, '-p', productName,
                  '--old-version=%s' % oldVersion,
                  '--old-app-version=%s' % oldAppVersion,
                  '--old-long-version=%s' % oldLongVersion,
                  '-v', version, '--app-version=%s' % appVersion,
                  '--long-version=%s' % longVersion,
                  '-n', str(buildNumber), '-a', ausServerUrl,
                  '-s', stagingServer, '-c', verifyConfigPath,
                  '-d', oldCandidatesDir, '-l', 'shipped-locales',
                  '--pretty-candidates-dir'],
         description=['bump', verifyConfig],
         workdir='tools'
        )
        self.addStep(ShellCommand,
         command=['hg', 'commit', '-m',
                  'Automated configuration bump: ' + \
                  '%s, from %s to %s build %s' % \
                    (verifyConfig, oldVersion, version, buildNumber)],
         description=['commit', verifyConfig],
         workdir='tools',
         haltOnFailure=True
        )
        self.addStep(ShellCommand,
         command=['hg', 'push', '-e',
                  'ssh -l %s %s' % (hgUsername, sshKeyOption),
                  '-f', pushRepo],
         description=['push updated', 'config'],
         workdir='tools',
         haltOnFailure=True
        )
        self.addStep(UpdateVerify,
         command=['bash', 'verify.sh', '-c', verifyConfig],
         workdir='tools/release/updates',
         description=['./verify.sh', verifyConfig]
        )

class ReleaseFinalVerification(ReleaseFactory):
    def __init__(self, linuxConfig, macConfig, win32Config, **kwargs):
        # MozillaBuildFactory needs the 'repoPath' argument, but we don't
        ReleaseFactory.__init__(self, repoPath='nothing', **kwargs)
        self.addStep(ShellCommand,
         command=['bash', 'final-verification.sh',
                  linuxConfig, macConfig, win32Config],
         description=['final-verification.sh'],
         workdir='tools/release'
        )

class UnittestBuildFactory(MozillaBuildFactory):
    def __init__(self, platform, productName, config_repo_path, config_dir,
            objdir, mochitest_leak_threshold=None, uploadPackages=False,
            unittestMasters=None, stageUsername=None, stageServer=None,
            stageSshKey=None, run_a11y=True, **kwargs):
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
        if unittestMasters is None:
            self.unittestMasters = []
        else:
            self.unittestMasters = unittestMasters

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

        if self.platform == 'win32':
            self.addStep(TinderboxShellCommand, name="kill sh",
             description='kill sh',
             descriptionDone="killed sh",
             command="pskill -t sh.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand, name="kill make",
             description='kill make',
             descriptionDone="killed make",
             command="pskill -t make.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand, name="kill firefox",
             description='kill firefox',
             descriptionDone="killed firefox",
             command="pskill -t firefox.exe",
             workdir="D:\\Utilities"
            )

        self.addStepNoEnv(Mercurial, mode='update',
         baseURL='http://%s/' % self.hgHost,
         defaultBranch=self.repoPath,
         timeout=60*60 # 1 hour
        )

        self.addPrintChangesetStep()

        self.addStep(ShellCommand,
         name="clean configs",
         command=['rm', '-rf', 'mozconfigs'],
         workdir='.'
        )

        self.addStep(ShellCommand,
         name="buildbot configs",
         command=['hg', 'clone', self.config_repo_url, 'mozconfigs'],
         workdir='.'
        )

        self.addCopyMozconfigStep()

        # TODO: Do we need this special windows rule?
        if self.platform == 'win32':
            self.addStep(ShellCommand, name="mozconfig contents",
             command=["type", ".mozconfig"]
            )
        else:
            self.addStep(ShellCommand, name='mozconfig contents',
             command=['cat', '.mozconfig']
            )

        self.addStep(ShellCommand,
         command=["make", "-f", "client.mk", "build"],
         description=['compile'],
         timeout=60*60, # 1 hour
         haltOnFailure=1
        )

        self.addStep(ShellCommand,
         command=['make', 'buildsymbols'],
         workdir='build/%s' % self.objdir,
        )

        if self.uploadPackages:
            self.addStep(ShellCommand,
             command=['make', 'package'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True
            )
            self.addStep(ShellCommand,
             command=['make', 'package-tests'],
             env=self.env,
             workdir='build/%s' % self.objdir,
             haltOnFailure=True
            )
            self.addStep(GetBuildID,
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
            postUploadCmd += ['--tinderbox-builds-dir %s-%s' % (self.branchName,
                                                                self.platform),
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

        platform_minidump_path = {
            'linux': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
            'win32': WithProperties('%(toolsdir:-)s/breakpad/win32/minidump_stackwalk.exe'),
            'macosx': WithProperties('%(toolsdir:-)s/breakpad/osx/minidump_stackwalk'),
            }

        self.env['MINIDUMP_STACKWALK'] = platform_minidump_path[self.platform]

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

        if self.platform == 'win32':
            self.addStep(unittest_steps.CreateProfileWin,
             warnOnWarnings=True,
             workdir="build",
             command = r'python testing\tools\profiles\createTestingProfile.py --clobber --binary %s\dist\bin\firefox.exe' % self.objdir,
            )
        else:
            self.addStep(unittest_steps.CreateProfile,
             warnOnWarnings=True,
             workdir="build",
             command = r'python testing/tools/profiles/createTestingProfile.py --clobber --binary %s/dist/bin/firefox' % self.objdir,
            )

        self.addStep(unittest_steps.MozillaReftest, warnOnWarnings=True,
         test_name="reftest",
         workdir="build/%s" % self.objdir,
         timeout=5*60, # 5 minutes.
        )
        self.addStep(unittest_steps.MozillaReftest, warnOnWarnings=True,
         test_name="crashtest",
         workdir="build/%s" % self.objdir,
        )
        self.addStep(unittest_steps.MozillaMochitest, warnOnWarnings=True,
         test_name="mochitest-plain",
         workdir="build/%s" % self.objdir,
         leakThreshold=mochitest_leak_threshold,
         timeout=5*60, # 5 minutes.
        )
        self.addStep(unittest_steps.MozillaMochitest, warnOnWarnings=True,
         test_name="mochitest-chrome",
         workdir="build/%s" % self.objdir,
        )
        self.addStep(unittest_steps.MozillaMochitest, warnOnWarnings=True,
         test_name="mochitest-browser-chrome",
         workdir="build/%s" % self.objdir,
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
        changesetLink = ''.join(['<a href=http://hg.mozilla.org/',
            self.repoPath,
            '/index.cgi/rev/%(got_revision)s title="Built from revision %(got_revision)s">rev:%(got_revision)s</a>'])
        self.addStep(ShellCommand,
         command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)],
        )

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

        self.addStep(ShellCommand, name="copy mozconfig",
         command=['cp', mozconfig, 'build/.mozconfig'],
         description=['copy mozconfig'],
         workdir='.'
        )

    def addPreTestSteps(self):
        pass

    def addPostTestSteps(self):
        pass

class CCUnittestBuildFactory(MozillaBuildFactory):
    def __init__(self, platform, config_repo_path, config_dir, objdir, mozRepoPath,
            productName=None, brandName=None, mochitest_leak_threshold=None,
            mochichrome_leak_threshold=None, mochibrowser_leak_threshold=None,
            exec_reftest_suites=True, exec_mochi_suites=True, run_a11y=True,
            **kwargs):
        self.env = {}
        MozillaBuildFactory.__init__(self, **kwargs)
        self.config_repo_path = config_repo_path
        self.mozRepoPath = mozRepoPath
        self.config_dir = config_dir
        self.objdir = objdir
        self.run_a11y = run_a11y
        self.productName = productName
        if brandName:
            self.brandName = brandName
        else:
            self.brandName = productName.capitalize()
        self.mochitest_leak_threshold = mochitest_leak_threshold
        self.mochichrome_leak_threshold = mochichrome_leak_threshold
        self.mochibrowser_leak_threshold = mochibrowser_leak_threshold
        self.exec_reftest_suites = exec_reftest_suites
        self.exec_mochi_suites = exec_mochi_suites

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

        if self.platform == 'win32':
            self.addStep(TinderboxShellCommand, name="kill hg",
             description='kill hg',
             descriptionDone="killed hg",
             command="pskill -t hg.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand, name="kill sh",
             description='kill sh',
             descriptionDone="killed sh",
             command="pskill -t sh.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand, name="kill make",
             description='kill make',
             descriptionDone="killed make",
             command="pskill -t make.exe",
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand, name="kill %s" % self.productName,
             description='kill %s' % self.productName,
             descriptionDone="killed %s" % self.productName,
             command="pskill -t %s.exe" % self.productName,
             workdir="D:\\Utilities"
            )
            self.addStep(TinderboxShellCommand, name="kill xpcshell",
             description='kill xpcshell',
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
         name="client.py checkout",
         command=['python', 'client.py', 'checkout']
        )

        self.addPrintMozillaChangesetStep()

        self.addStep(ShellCommand,
         name="clean configs",
         command=['rm', '-rf', 'mozconfigs'],
         workdir='.'
        )

        self.addStep(ShellCommand,
         name="buildbot configs",
         command=['hg', 'clone', self.config_repo_url, 'mozconfigs'],
         workdir='.'
        )

        self.addCopyMozconfigStep()

        self.addStep(ShellCommand, name='mozconfig contents',
         command=['cat', '.mozconfig']
        )

        self.addStep(ShellCommand,
         command=["make", "-f", "client.mk", "build"],
         description=['compile'],
         timeout=60*60, # 1 hour
         haltOnFailure=1
        )
        self.addStep(ShellCommand,
                     command=['make', 'buildsymbols'],
                     workdir='build/%s' % self.objdir,
                     )

        self.addStep(SetProperty,
         command=['bash', '-c', 'pwd'],
         property='toolsdir',
         workdir='tools'
        )

        platform_minidump_path = {
            'linux': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
            'win32': WithProperties('%(toolsdir:-)s/breakpad/win32/minidump_stackwalk.exe'),
            'macosx': WithProperties('%(toolsdir:-)s/breakpad/osx/minidump_stackwalk'),
            }

        self.env['MINIDUMP_STACKWALK'] = platform_minidump_path[self.platform]

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

        if platform == 'win32':
            self.addStep(unittest_steps.CreateProfileWin,
             warnOnWarnings=True,
             workdir="build",
             command = r'python mozilla\testing\tools\profiles\createTestingProfile.py --clobber --binary %s\mozilla\dist\bin\%s.exe' %
                          (self.objdir, self.productName),
            )
        else:
            if platform == 'macosx':
                app_run = '%s.app/Contents/MacOS/%s-bin' % (self.brandName, self.productName)
            else:
                app_run = 'bin/%s' % self.productName
            self.addStep(unittest_steps.CreateProfile,
             warnOnWarnings=True,
             workdir="build",
             command = r'python mozilla/testing/tools/profiles/createTestingProfile.py --clobber --binary %s/mozilla/dist/%s' %
                          (self.objdir, app_run),
            )

        if self.exec_reftest_suites:
            self.addStep(unittest_steps.MozillaReftest, warnOnWarnings=True,
             test_name="reftest",
             workdir="build/%s" % self.objdir,
             timeout=5*60,
            )
            self.addStep(unittest_steps.MozillaReftest, warnOnWarnings=True,
             test_name="crashtest",
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
        self.addStep(ShellCommand,
         command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)],
        )

    def addPrintMozillaChangesetStep(self):
        self.addStep(SetProperty,
         command=['hg', 'identify', '-i'],
         workdir='build/mozilla',
         property='hg_revision'
        )
        changesetLink = '<a href=http://%s/%s/rev' % (self.hgHost, self.mozRepoPath)
        changesetLink += '/%(hg_revision)s title="Built from Mozilla revision %(hg_revision)s">moz:%(hg_revision)s</a>'
        self.addStep(ShellCommand,
         command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)]
        )

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

        self.addStep(ShellCommand, name="copy mozconfig",
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

        self.addStep(ShellCommand, name="copy mozconfig",
         command=['cp', mozconfig, 'build/.mozconfig'],
         description=['copy mozconfig'],
         workdir='.'
        )

    def addInitialSteps(self):
        # Always clobber code coverage builds
        self.addStep(ShellCommand,
         command=['rm', '-rf', 'build'],
         workdir=".",
         timeout=30*60,
        )
        UnittestBuildFactory.addInitialSteps(self)

    def addPreTestSteps(self):
        self.addStep(ShellCommand,
         command=['mv','bin','bin-original'],
         workdir="build/%s/dist" % self.objdir,
        )
        self.addStep(ShellCommand,
         command=['jscoverage', '--mozilla',
                  '--no-instrument=defaults',
                  '--no-instrument=greprefs',
                  '--no-instrument=chrome/browser/content/browser/places/treeView.js',
                  'bin-original', 'bin'],
         workdir="build/%s/dist" % self.objdir,
        )

    def addPostTestSteps(self):
        self.addStep(ShellCommand,
         command=['lcov', '-c', '-d', '.', '-o', 'app.info'],
         workdir="build/%s" % self.objdir,
        )
        self.addStep(ShellCommand,
         command=['rm', '-rf', 'codecoverage_html'],
         workdir="build",
        )
        self.addStep(ShellCommand,
         command=['mkdir', 'codecoverage_html'],
         workdir="build",
        )
        self.addStep(ShellCommand,
         command=['genhtml', '../%s/app.info' % self.objdir],
         workdir="build/codecoverage_html",
        )
        self.addStep(ShellCommand,
         command=['cp', '%s/dist/bin/application.ini' % self.objdir, 'codecoverage_html'],
         workdir="build",
        )
        tarfile = "codecoverage-%s.tar.bz2" % self.branchName
        self.addStep(ShellCommand,
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
         env=uploadEnv,
         command=['python', 'build/upload.py', tarfile],
         workdir="build",
        )
        # Clean up after ourselves
        self.addStep(ShellCommand,
         command=['rm', '-rf', 'build'],
         workdir=".",
         timeout=30*60,
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
         description=['remove', 'verify', 'dir'],
         descriptionDone=['removed', 'verify', 'dir'],
         command=['rm', '-rf', verifyDir],
         workdir='.',
         haltOnFailure=True,
        )

        self.addStep(ShellCommand,
         description=['(re)create', 'verify', 'dir'],
         descriptionDone=['(re)created', 'verify', 'dir'],
         command=['bash', '-c', 'mkdir -p ' + verifyDirVersion], 
         workdir='.',
         haltOnFailure=True,
        )
        
        # Download current release
        self.addStep(ShellCommand,
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
                         description=['(re)create', 'product', 'dir'],
                         descriptionDone=['(re)created', 'product', 'dir'],
                         command=['bash', '-c', 'mkdir -p %s/%s' % (verifyDirVersion, product)], 
                         workdir='.',
                         haltOnFailure=True,
                        )
            self.addStep(ShellCommand,
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
                 env=None, **kwargs):
        """
    mobileRepoPath: the path to the mobileRepo (mobile-browser)
    platform: the mobile platform (linux-arm, wince-arm)
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
        self.mozconfig = 'configs/%s/%s/mozconfig' % (self.configSubDir,
                                                      mozconfig)
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
                       changesetLink=None):
        assert (repository and workdir)
        if (targetDirectory == None):
            targetDirectory = self.getRepoName(repository)

        self.addStep(ShellCommand,
            command=['bash', '-c',
                     'if [ ! -d %s ]; then hg clone %s %s; fi' %
                     (targetDirectory, repository, targetDirectory)],
            workdir=workdir,
            description=['checking', 'out', targetDirectory],
            descriptionDone=['checked', 'out', targetDirectory],
            timeout=cloneTimeout
        )
        self.addStep(ShellCommand,
            command=['hg', 'pull', '-u'],
            workdir="%s/%s" % (workdir, targetDirectory),
            description=['updating', targetDirectory],
            descriptionDone=['updated', targetDirectory],
            haltOnFailure=True
        )
        if changesetLink:
            self.addStep(GetHgRevision(
                workdir='%s/%s' % (workdir, targetDirectory)
            ))
            self.addStep(ShellCommand(
                command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)]
            ))

    def getMozconfig(self):
        self.addHgPullSteps(repository=self.configRepository,
                            workdir=self.baseWorkDir,
                            targetDirectory='configs')
        self.addStep(ShellCommand,
            command=['cp', self.mozconfig,
                     '%s/.mozconfig' % self.branchName],
            workdir=self.baseWorkDir,
            description=['copying', 'mozconfig'],
            descriptionDone=['copied', 'mozconfig'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=['cat', '.mozconfig'],
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['cat', 'mozconfig']
        )

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
            releaseToLatest=self.nightly,
            releaseToDated=self.nightly,
            releaseToTinderboxBuilds=True,
            tinderboxBuildsDir=self.baseUploadDir,
            dependToDated=True,
            workdir='%s/%s/%s' % (self.baseWorkDir, self.branchName,
                                        self.objdir)
        )


class MaemoBuildFactory(MobileBuildFactory):
    def __init__(self, scratchboxPath="/scratchbox/moz_scratchbox",
                 packageGlob="mobile/dist/*.tar.bz2 " +
                 "xulrunner/xulrunner/*.deb mobile/mobile/*.deb " +
                 "xulrunner/dist/*.tar.bz2",
                 **kwargs):
        MobileBuildFactory.__init__(self, **kwargs)
        self.packageGlob = packageGlob
        self.scratchboxPath = scratchboxPath

        self.addPrecleanSteps()
        self.addHgPullSteps(repository=self.repository,
                            workdir=self.baseWorkDir,
                            changesetLink=self.mozChangesetLink,
                            cloneTimeout=60*30)
        self.addHgPullSteps(repository=self.mobileRepository,
                            workdir='%s/%s' % (self.baseWorkDir,
                                               self.branchName),
                            changesetLink=self.mobileChangesetLink,
                            targetDirectory='mobile')
        self.getMozconfig()
        self.addBuildSteps()
        self.addPackageSteps()
        self.addUploadSteps(platform='linux')

    def addPrecleanSteps(self):
        self.addStep(ShellCommand,
            command = 'rm -f /tmp/*_cltbld.log',
            description=['removing', 'logfile'],
            descriptionDone=['removed', 'logfile']
        )
        if self.nightly:
            self.addStep(ShellCommand,
                command=['rm', '-rf', self.branchName],
                env=self.env,
                workdir=self.baseWorkDir,
                timeout=60*60
            )
        else:
            self.addStep(ShellCommand,
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

    def addBuildSteps(self):
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s' % self.branchName,
                     'make -f client.mk build'],
            description=['compile'],
            env={'PKG_CONFIG_PATH': '/usr/lib/pkgconfig:/usr/local/lib/pkgconfig'},
            haltOnFailure=True
        )

    def addPackageSteps(self):
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s/%s/mobile' % (self.branchName, self.objdir),
                     'make package'],
            description=['make', 'package'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s/%s/xulrunner' % (self.branchName, self.objdir),
                     'make package-tests PYTHON=python2.5'],
            description=['make', 'package-tests'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s/%s/mobile' % (self.branchName,
                                                self.objdir),
                     'make deb'],
            description=['make', 'mobile', 'deb'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     'build/%s/%s/xulrunner' % (self.branchName,
                                                self.objdir),
                     'make deb'],
            description=['make', 'xulrunner', 'deb'],
            haltOnFailure=True
        )

class WinceBuildFactory(MobileBuildFactory):
    def __init__(self,
                 packageGlob="xulrunner/dist/*.zip mobile/dist/*.zip mobile/dist/*.cab",
                 **kwargs):
        MobileBuildFactory.__init__(self, **kwargs)
        self.packageGlob = packageGlob

        self.addPrecleanSteps()
        self.addHgPullSteps(repository=self.repository,
                            workdir=self.baseWorkDir,
                            changesetLink=self.mozChangesetLink,
                            cloneTimeout=60*30)
        self.addHgPullSteps(repository=self.mobileRepository,
                            workdir='%s/%s' % (self.baseWorkDir,
                                               self.branchName),
                            changesetLink=self.mobileChangesetLink,
                            targetDirectory='mobile')
        self.getMozconfig()
        self.addBuildSteps()
        self.addPackageSteps()
        self.addUploadSteps(platform='win32')

    def addPrecleanSteps(self):
        if self.nightly:
            self.addStep(ShellCommand,
                command=['rm', '-rf', self.branchName],
                env=self.env,
                workdir=self.baseWorkDir,
                timeout=60*60
            )
        else:
            self.addStep(ShellCommand,
                command = ['bash', '-c', 'rm -rf %s/%s/mobile/dist/*.zip ' %
                           (self.branchName, self.objdir) +
                           '%s/%s/mobile/dist/*.cab ' %
                           (self.branchName, self.objdir) +
                           '%s/%s/xulrunner/dist/*.zip' %
                           (self.branchName, self.objdir)],
                workdir=self.baseWorkDir,
                description=['removing', 'old', 'builds'],
                descriptionDone=['removed', 'old', 'builds']
            )

    def addBuildSteps(self):
        self.addStep(ShellCommand,
            command=['make', '-f', 'client.mk', 'build'],
            description=['compile'],
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            env=self.env,
            haltOnFailure=True
        )

    def addPackageSteps(self):
        self.addStep(ShellCommand,
            command=['make', 'package'],
            workdir='%s/%s/%s/mobile' % (self.baseWorkDir, self.branchName,
                                         self.objdir),
            description=['make', 'package'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=['make', 'installer'],
            workdir='%s/%s/%s/mobile' % (self.baseWorkDir, self.branchName,
                                         self.objdir),
            description=['make', 'installer'],
            haltOnFailure=True
        )

packagedUnittestSuites = ['reftest', 'crashtest', 'xpcshell', 'mochitest-plain',
                          'mochitest-chrome', 'mochitest-browser-chrome',
                          'mochitest-a11y']
class UnittestPackagedBuildFactory(MozillaBuildFactory):
    def __init__(self, platform, env=None, test_suites=None,
            mochitest_leak_threshold=None, **kwargs):
        if env is None:
            self.env = MozillaEnvironments['%s-unittest' % platform].copy()
        else:
            self.env = env

        self.platform = platform.split('-')[0]
        assert self.platform in ('linux', 'linux64', 'win32', 'macosx')

        MozillaBuildFactory.__init__(self, **kwargs)

        if test_suites is None:
            self.test_suites = packagedUnittestSuites
        else:
            self.test_suites = test_suites

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
         workdir='symbols',
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
         workdir='symbols',
        ))

        # Find firefox!
        if platform == "macosx":
            self.addStep(FindFile(
             filename="firefox",
             directory=".",
             max_depth=4,
             property_name="exepath",
             name="Find executable",
            ))
        elif platform == "win32":
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="firefox/firefox.exe",
            ))
        else:
            self.addStep(SetBuildProperty(
             property_name="exepath",
             value="firefox/firefox",
            ))

        def get_exedir(build):
            return os.path.dirname(build.getProperty('exepath'))
        self.addStep(SetBuildProperty(
         property_name="exedir",
         value=get_exedir,
        ))

        # Set up the stack walker
        self.addStep(SetProperty,
         command=['bash', '-c', 'pwd'],
         property='toolsdir',
         workdir='tools'
        )

        platform_minidump_path = {
            'linux': WithProperties('%(toolsdir:-)s/breakpad/linux/minidump_stackwalk'),
            'win32': WithProperties('%(toolsdir:-)s/breakpad/win32/minidump_stackwalk.exe'),
            'macosx': WithProperties('%(toolsdir:-)s/breakpad/osx/minidump_stackwalk'),
            }

        self.env['MINIDUMP_STACKWALK'] = platform_minidump_path[self.platform]

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

        changesetLink = '<a href="%(repo_path)s/rev/%(revision)s"' + \
                ' title="Built from revision %(revision)s">rev:%(revision)s</a>'
        self.addStep(ShellCommand,
         command=['echo', 'TinderboxPrint:', WithProperties(changesetLink)],
        )

        # Run them!
        for suite in self.test_suites:
            if suite.startswith('mochitest'):
                variant = suite.split('-', 1)[1]
                self.addStep(unittest_steps.MozillaPackagedMochitests(
                 variant=variant,
                 env=self.env,
                 symbols_path='symbols',
                 leakThreshold=mochitest_leak_threshold,
                ))
            elif suite == 'xpcshell':
                self.addStep(unittest_steps.MozillaPackagedXPCShellTests(
                 env=self.env,
                 symbols_path='symbols',
                ))
            elif suite in ('reftest', 'crashtest'):
                crashtest = (suite == "crashtest")
                self.addStep(unittest_steps.MozillaPackagedReftests(
                 crashtest=crashtest,
                 env=self.env,
                 symbols_path='symbols',
                ))

    def addInitialSteps(self):
        self.addStep(ShellCommand(command=['rm', '-rf', 'build'], workdir='.'))
        MozillaBuildFactory.addInitialSteps(self)

class TalosFactory(BuildFactory):
    """Create working talos build factory"""

    winClean   = ["touch temp.zip &", "rm", "-rf", "*.zip", "talos/",
                  "firefox/", "symbols/"]
    macClean   = "rm -vrf *"
    linuxClean = "rm -vrf *"

    def __init__(self, OS, toolsDir, envName, buildBranch, branchName,
            configOptions, talosCmd, customManifest='', customTalos=None,
            workdirBase=None, fetchSymbols=False,
            cvsRoot=":pserver:anonymous@cvs-mirror.mozilla.org:/cvsroot"):
        BuildFactory.__init__(self)
        if OS in ('linux', 'linuxbranch',):
            cleanCmd = self.linuxClean
        elif OS in ('win',):
            cleanCmd = self.winClean
        else:
            cleanCmd = self.macClean
        if workdirBase is None:
            workdirBase = "."
        self.addStep(ShellCommand(
         workdir=workdirBase,
         description="Cleanup",
         command=cleanCmd,
         env=MozillaEnvironments[envName])
        )
        if OS in ('leopard', 'tiger'):
            self.addStep(FileDownload(
             mastersrc="%s/buildfarm/utils/installdmg.ex" % toolsDir,
             slavedest="installdmg.ex",
             workdir=workdirBase,
            ))

        self.addStep(FileDownload(
         mastersrc="%s/buildfarm/maintenance/count_and_reboot.py" % toolsDir,
         slavedest="count_and_reboot.py",
         workdir=workdirBase,
        ))

        if customManifest != '':
            self.addStep(FileDownload(
             mastersrc=customManifest,
             slavedest="manifest.txt",
             workdir=os.path.join(workdirBase, "talos/page_load_test"))
            )

        if customTalos is None:
            self.addStep(ShellCommand(
             command=["cvs", "-d", cvsRoot, "co", "-d", "talos",
                      "mozilla/testing/performance/talos"],
             workdir=workdirBase,
             description="checking out talos",
             haltOnFailure=True,
             flunkOnFailure=True,
             env=MozillaEnvironments[envName])
            )
            self.addStep(FileDownload(
             mastersrc="%s/buildfarm/utils/generate-tpcomponent.py" % toolsDir,
             slavedest="generate-tpcomponent.py",
             workdir=os.path.join(workdirBase, "talos/page_load_test"))
            )
            self.addStep(ShellCommand(
             command=["python", "generate-tpcomponent.py"],
             workdir=os.path.join(workdirBase, "talos/page_load_test"),
             description="setting up pageloader",
             haltOnFailure=True,
             flunkOnFailure=True,
             env=MozillaEnvironments[envName])
            )
        else:
            self.addStep(FileDownload(
             mastersrc=customTalos,
             slavedest=customTalos,
             workdir=workdirBase,
            ))
            self.addStep(UnpackFile(
             filename=customTalos,
             workdir=workdirBase,
            ))

        def get_url(build):
            return build.source.changes[-1].files[0]
        self.addStep(DownloadFile(
         url_fn=get_url,
         url_property="fileURL",
         filename_property="filename",
         workdir=workdirBase,
         name="Download build",
        ))
        if fetchSymbols:
            def get_symbols_url(build):
                suffixes = ('.tar.bz2', '.dmg', '.zip')
                buildURL = build.getProperty('fileURL')

                for suffix in suffixes:
                    if buildURL.endswith(suffix):
                        return buildURL[:-len(suffix)] + '.crashreporter-symbols.zip'

            self.addStep(DownloadFile(
             url_fn=get_symbols_url,
             filename_property="symbolsFile",
             workdir=workdirBase,
             name="Download symbols",
            ))
            self.addStep(UnpackFile(
             filename=WithProperties("%(symbolsFile)s"),
             workdir=workdirBase,
             name="Unpack symbols",
            ))
        self.addStep(UnpackFile(
         filename=WithProperties("%(filename)s"),
         workdir=workdirBase,
         name="Unpack build",
        ))
        if OS == 'win':
            self.addStep(ShellCommand(
             workdir=os.path.join(workdirBase, "firefox/"),
             flunkOnFailure=False,
             warnOnFailure=False,
             description="chmod files (see msys bug)",
             command=["chmod", "-v", "-R", "a+x", "."],
             env=MozillaEnvironments[envName])
            )
        if OS in ('tiger', 'leopard'):
            self.addStep(FindFile(
             workdir=os.path.join(workdirBase, "talos"),
             filename="firefox",
             directory="..",
             max_depth=4,
             property_name="exepath",
             name="Find executable",
            ))
            exepath = WithProperties('%(exepath)s')
        else:
            exepath = '../firefox/firefox'
        self.addStep(talos_steps.MozillaUpdateConfig(
         workdir=os.path.join(workdirBase, "talos/"),
         branch=buildBranch,
         branchName=branchName,
         haltOnFailure=True,
         executablePath=exepath,
         addOptions=configOptions,
         env=MozillaEnvironments[envName],
         useSymbols=fetchSymbols)
        )
        self.addStep(talos_steps.MozillaRunPerfTests(
         warnOnWarnings=True,
         workdir=os.path.join(workdirBase, "talos/"),
         timeout=21600,
         haltOnFailure=False,
         command=talosCmd,
         env=MozillaEnvironments[envName])
        )
        self.addStep(ShellCommand(
         flunkOnFailure=False,
         warnOnFailure=False,
         alwaysRun=True,
         workdir=workdirBase,
         description="reboot after 1 test run",
         command=["python", "count_and_reboot.py", "-f", "../talos_count.txt", "-n", "1", "-z"],
         env=MozillaEnvironments[envName],
        ))

class MobileNightlyRepackFactory(BaseRepackFactory):
    extraConfigureArgs = []

    def __init__(self, hgHost=None, enUSBinaryURL=None,
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
         command=['sh', '-c', 'if [ -d mobile ]; then ' +
                  'hg -R mobile pull -r default ; else ' +
                  'hg clone http://' + self.hgHost + '/' + self.mobileRepoPath +
                  ' mobile ; ' +
                  'fi && hg -R mobile update -r default'],
         descriptionDone=['en-US', 'mobile', 'source'],
         workdir='%s/%s' % (self.baseWorkDir, self.branchName),
         timeout=30*60 # 30 minutes
        )

    def updateSources(self):
        self.addStep(ShellCommand,
         command=['hg', 'up', '-C', '-r', 'default'],
         description='update workdir',
         workdir=WithProperties(self.baseWorkDir + '/' + self.l10nRepoPath + \
                                '/%(locale)s'),
         haltOnFailure=True
        )

    def getMozconfig(self):
        pass

    def downloadBuilds(self):
        self.addStep(ShellCommand,
         command=['make', 'wget-en-US'],
         descriptionDone='wget en-US',
         env={'EN_US_BINARY_URL': self.enUSBinaryURL},
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.branchName,
                                       self.appName)
        )

    def updateEnUS(self):
        self.addStep(ShellCommand,
         command=['make', 'unpack'],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.branchName,
                                       self.appName)
        )
        self.addStep(SetProperty,
         command=['make', 'ident'],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.branchName,
                                       self.appName),
         extract_fn=identToProperties()
        )
        self.addStep(ShellCommand,
         command=['hg', 'update', '-r', WithProperties('%(gecko_revision)s')],
         haltOnFailure=True,
         workdir='%s/%s' % (self.baseWorkDir, self.branchName)
        )
        self.addStep(ShellCommand,
         command=['hg', 'update', '-r', WithProperties('%(fennec_revision)s')],
         haltOnFailure=True,
         workdir='%s/%s/%s' % (self.baseWorkDir, self.branchName, self.appName)
        )

    def doRepack(self):
        self.addStep(ShellCommand,
         command=['make', WithProperties('installers-%(locale)s')],
         haltOnFailure=True,
         workdir='%s/%s/%s/locales' % (self.baseWorkDir, self.branchName,
                                       self.appName)
        )

    # Upload targets aren't defined in mobile/locales/Makefile,
    # so use MozillaStageUpload for now.
    def addUploadSteps(self, platform):
        self.addStep(SetProperty,
            command=['python', 'config/printconfigsetting.py',
                     'dist/l10n-stage/%s/application.ini' % self.project,
                     'App', 'BuildID'],
            property='buildid',
            workdir='%s/%s' % (self.baseWorkDir, self.branchName),
            description=['getting', 'buildid'],
            descriptionDone=['got', 'buildid']
        )
        self.addStep(MozillaStageUpload,
            objdir="%s/dist" % (self.branchName),
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
            releaseToLatest=True,
            releaseToDated=True,
            releaseToTinderboxBuilds=True,
            tinderboxBuildsDir=self.baseUploadDir,
            dependToDated=True,
            workdir='%s/%s/dist' % (self.baseWorkDir, self.branchName)
        )

    def doUpload(self):
        pass



class MaemoNightlyRepackFactory(MobileNightlyRepackFactory):
    extraConfigureArgs = ['--target=arm-linux']

    def __init__(self, **kwargs):
        MobileNightlyRepackFactory.__init__(self, **kwargs)

    def doUpload(self):
        self.addUploadSteps(platform='linux')
