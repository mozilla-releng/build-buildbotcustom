# The absolute_import directive looks firstly at what packages are available
# on sys.path to avoid name collisions when we import release.* from elsewhere
from __future__ import absolute_import

import os
from buildbot.process.buildstep import regex_log_evaluator
from buildbot.scheduler import Scheduler, Dependent, Triggerable
from buildbot.status.tinderbox import TinderboxMailNotifier
from buildbot.status.mail import MailNotifier
from buildbot.steps.trigger import Trigger
from buildbot.steps.shell import WithProperties
from buildbot.status.builder import Results
from buildbot.process.factory import BuildFactory

import release.platforms
import release.paths
import buildbotcustom.changes.ftppoller
import buildbotcustom.common
import build.paths
import release.info
reload(release.platforms)
reload(release.paths)
reload(buildbotcustom.changes.ftppoller)
reload(build.paths)
reload(release.info)

from buildbotcustom.status.mail import ChangeNotifier
from buildbotcustom.misc import get_l10n_repositories, \
  generateTestBuilderNames, generateTestBuilder, _nextFastReservedSlave, \
  makeLogUploadCommand, changeContainsProduct, nomergeBuilders, \
  changeContainsProperties
from buildbotcustom.common import reallyShort
from buildbotcustom.process.factory import StagingRepositorySetupFactory, \
  ScriptFactory, SingleSourceFactory, ReleaseBuildFactory, \
  ReleaseUpdatesFactory, ReleaseFinalVerification, L10nVerifyFactory, \
  PartnerRepackFactory, MajorUpdateFactory, XulrunnerReleaseBuildFactory, \
  TuxedoEntrySubmitterFactory, makeDummyBuilder, get_signing_cmd, \
  SigningScriptFactory
from buildbotcustom.changes.ftppoller import UrlPoller
from release.platforms import buildbot2ftp
from release.paths import makeCandidatesDir
from buildbotcustom.scheduler import TriggerBouncerCheck, \
     makePropertiesScheduler, AggregatingScheduler
from buildbotcustom.misc_scheduler import buildIDSchedFunc, buildUIDSchedFunc
from buildbotcustom.status.errors import update_verify_error, \
     permission_check_error
from buildbotcustom.status.queued_command import QueuedCommandHandler
from build.paths import getRealpath
from release.info import getRuntimeTag, getReleaseTag
from mozilla_buildtools.queuedir import QueueDir
import BuildSlaves

DEFAULT_PARALLELIZATION = 10

def generateReleaseBranchObjects(releaseConfig, branchConfig,
                                 releaseConfigFile, sourceRepoKey="mozilla",
                                 secrets=None):
    # This variable is one thing that forces us into reconfiging prior to a
    # release. It should be removed as soon as nothing depends on it.
    sourceRepoInfo = releaseConfig['sourceRepositories'][sourceRepoKey]
    releaseTag = getReleaseTag(releaseConfig['baseTag'])
    # This tag is created post-signing, when we do some additional
    # config file bumps
    runtimeTag = getRuntimeTag(releaseTag)
    l10nChunks = releaseConfig.get('l10nChunks', DEFAULT_PARALLELIZATION)
    updateVerifyChunks = releaseConfig.get('updateVerifyChunks', DEFAULT_PARALLELIZATION)
    tools_repo_path = releaseConfig.get('build_tools_repo_path',
                                        branchConfig['build_tools_repo_path'])
    tools_repo = '%s%s' % (branchConfig['hgurl'], tools_repo_path)
    config_repo = '%s%s' % (branchConfig['hgurl'],
                             branchConfig['config_repo_path'])

    branchConfigFile = getRealpath('localconfig.py')
    unix_slaves = []
    all_slaves = []
    for p in branchConfig['platforms']:
        platform_slaves = branchConfig['platforms'][p].get('slaves', [])
        all_slaves.extend(platform_slaves)
        if 'win' not in p:
            unix_slaves.extend(platform_slaves)
    unix_slaves = [x for x in set(unix_slaves)]
    all_slaves = [x for x in set(all_slaves)]

    signedPlatforms = ()
    if not releaseConfig.get('enableSigningAtBuildTime', True):
        signedPlatforms = releaseConfig.get('signedPlatforms', ('win32',))
    if secrets is None:
        secrets = {}
    signingServers = secrets.get('release-signing')
    if releaseConfig.get('enableSigningAtBuildTime', True):
        assert signingServers, 'Please provide a valid list of signing servers'

    def builderPrefix(s, platform=None):
        if platform:
            return "release-%s-%s_%s" % (sourceRepoInfo['name'], platform, s)
        else:
            return "release-%s-%s" % (sourceRepoInfo['name'], s)

    def releasePrefix():
        """Construct a standard format product release name from the
           product name, version and build number stored in release_config.py
        """
        return "%s %s build%s" % (
            releaseConfig['productName'].title(),
            releaseConfig['version'],
            releaseConfig['buildNumber'], )

    def majorReleasePrefix():
        return "%s %s build%s" % (
            releaseConfig['productName'].title(),
            releaseConfig['majorUpdateToVersion'],
            releaseConfig['majorUpdateBuildNumber'], )

    def genericFtpUrl():
        """ Generate an FTP URL pointing to the uploaded release builds for
        sticking into release notification messages """
        return makeCandidatesDir(
            releaseConfig['productName'],
            releaseConfig['version'],
            releaseConfig['buildNumber'],
            protocol='ftp',
            server=releaseConfig['ftpServer'])

    def genericHttpsUrl():
        """ Generate an HTTPS URL pointing to the uploaded release builds for
        sticking into release notification messages """
        return makeCandidatesDir(
            releaseConfig['productName'],
            releaseConfig['version'],
            releaseConfig['buildNumber'],
            protocol='https',
            server=releaseConfig['ftpServer'])

    def createReleaseMessage(mode, name, build, results, master_status):
        """Construct a standard email to send to release@/release-drivers@
           whenever a major step of the release finishes
        """
        msgdict = {}
        releaseName = releasePrefix()
        job_status = "failed" if results else "success"
        job_status_repr = Results[results]
        allplatforms = list(releaseConfig['enUSPlatforms'])
        xrplatforms = list(releaseConfig.get('xulrunnerPlatforms', []))
        stage = name.replace(builderPrefix(""), "")
        # Detect platform from builder name by tokenizing by '_', and matching
        # the first token after the prefix
        if stage.startswith("xulrunner"):
            platform = ["xulrunner_%s" % p for p in xrplatforms
                if stage.replace("xulrunner_", "").split('_')[0] == p]
        else:
            platform = [p for p in allplatforms if stage.split('_')[0] == p]
        if releaseConfig.get('majorUpdateRepoPath'):
            majorReleaseName = majorReleasePrefix()
        platform = platform[0] if len(platform) >= 1 else ''
        bare_platform = platform.replace('xulrunner_', '')
        message_tag = releaseConfig.get('messagePrefix', '[release] ')
        buildbot_url = ''
        if master_status.getURLForThing(build):
            buildbot_url = "Full details are available at:\n %s\n" % master_status.getURLForThing(build)
        # Use a generic ftp URL non-specific to any locale
        ftpURL = genericFtpUrl()
        if 'xulrunner' in platform:
            ftpURL = ftpURL.replace(releaseConfig['productName'], 'xulrunner')
        isPlatformUnsigned = False
        if platform:
            platformDir = buildbot2ftp(bare_platform)
            if 'xulrunner' in platform:
                platformDir = ''
            if bare_platform in signedPlatforms:
                platformDir = 'unsigned/%s' % platformDir
                isPlatformUnsigned = True
            ftpURL = '/'.join([
                ftpURL.strip('/'),
                platformDir])

        stage = stage.replace("%s_" % platform, "") if platform else stage
        #try to load a unique message template for the platform(if defined, step and results
        #if none exists, fall back to the default template
        possible_templates = ["%s/%s_%s_%s" % (releaseConfig['releaseTemplates'], platform, stage, job_status),
            "%s/%s_%s" % (releaseConfig['releaseTemplates'], stage, job_status),
            "%s/%s_default_%s" % (releaseConfig['releaseTemplates'], platform, job_status),
            "%s/default_%s" % (releaseConfig['releaseTemplates'], job_status)]
        if isPlatformUnsigned:
            unsigned_templates = []
            for t in possible_templates:
                path, template = os.path.split(t)
                unsigned_templates.append(
                    os.path.join(path, 'unsigned_%s' % template))
            possible_templates = possible_templates + unsigned_templates
        template = None
        for t in possible_templates:
            if os.access(t, os.R_OK):
                template = open(t, "r", True)
                break

        if template:
            subject = message_tag + template.readline().strip() % locals()
            body = ''.join(template.readlines())
            template.close()
        else:
            raise IOError("Cannot find a template file to use")
        msgdict['subject'] = subject % locals()
        msgdict['body'] = body % locals() + "\n"
        msgdict['type'] = 'plain'
        return msgdict

    def createReleaseChangeMessage(change):
        """Construct a standard email to send to release@/release-drivers@
           whenever a change is pushed to a release-related branch being
           listened on"""
        msgdict = {}
        releaseName = releasePrefix()
        message_tag = releaseConfig.get('messagePrefix', '[release] ')
        step = None
        ftpURL = genericFtpUrl()
        if change.branch.endswith('signing'):
            step = "signing"
        else:
            step = "tag"
        #try to load a unique message template for the change
        #if none exists, fall back to the default template
        possible_templates = ("%s/%s_change" % (releaseConfig['releaseTemplates'], step),
            "%s/default_change" % releaseConfig['releaseTemplates'])
        template = None
        for t in possible_templates:
            if os.access(t, os.R_OK):
                template = open(t, "r", True)
                break

        if template:
            subject = message_tag + template.readline().strip() % locals()
            body = ''.join(template.readlines()) + "\n"
            template.close()
        else:
            raise IOError("Cannot find a template file to use")
        msgdict['subject'] = subject % locals()
        msgdict['body'] = body % locals()
        msgdict['type'] = 'plain'
        return msgdict

    def createReleaseAVVendorsMessage(mode, name, build, results, master_status):
        """Construct the release notification email to send to the AV Vendors.
        """
        template_name = "%s/updates_avvendors" % releaseConfig['releaseTemplates']
        if not os.access(template_name, os.R_OK):
            raise IOError("Cannot find a template file to use")

        template = open(template_name, "r", True)
        subject = '%(productName)s %(version)s release'
        body = ''.join(template.readlines())
        template.close()

        productName = releaseConfig['productName'].title()
        version = releaseConfig['version']
        buildsURL = genericHttpsUrl()

        msgdict = {}
        msgdict['subject'] = subject % locals()
        msgdict['body'] = body % locals() + "\n"
        msgdict['type'] = 'plain'
        return msgdict

    def parallelizeBuilders(base_name, platform, chunks):
        builders = {}
        for n in range(1, chunks+1):
            builders[n] = builderPrefix("%s_%s/%s" % (base_name, n,
                                                      str(chunks)),
                                        platform)
        return builders

    def l10nBuilders(platform):
        return parallelizeBuilders("repack", platform, l10nChunks)

    def updateVerifyBuilders(platform):
        return parallelizeBuilders("update_verify", platform,
                                   updateVerifyChunks)

    def majorUpdateVerifyBuilders(platform):
        return parallelizeBuilders("major_update_verify", platform,
                                   updateVerifyChunks)

    builders = []
    test_builders = []
    schedulers = []
    change_source = []
    important_builders = []
    status = []
    updates_upstream_builders = []
    post_signing_builders = []
    post_update_builders = []
    deliverables_builders = []
    post_deliverables_builders = []
    post_antivirus_builders = []

    ##### Builders
    builder_env = {
        'BUILDBOT_CONFIGS': '%s%s' % (branchConfig['hgurl'],
                                      branchConfig['config_repo_path']),
        'BUILDBOTCUSTOM': '%s%s' % (branchConfig['hgurl'],
                                    branchConfig['buildbotcustom_repo_path']),
        'CLOBBERER_URL': branchConfig['base_clobber_url']
    }

    if releaseConfig.get('enable_repo_setup'):
        if not releaseConfig.get('skip_repo_setup'):
            clone_repositories = dict()
            # The repo_setup builder only needs to the repoPath, so we only
            # give it that
            for sr in releaseConfig['sourceRepositories'].values():
                clone_repositories.update({sr['clonePath']: {}})
            # get_l10n_repositories spits out more than just the repoPath
            # It's easier to just pass it along rather than strip it out
            if len(releaseConfig['l10nPlatforms']) > 0:
                l10n_clone_repos = get_l10n_repositories(
                    releaseConfig['l10nRevisionFile'],
                    releaseConfig['l10nRepoClonePath'],
                    sourceRepoInfo['relbranch'])
                clone_repositories.update(l10n_clone_repos)

            repository_setup_factory = StagingRepositorySetupFactory(
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=tools_repo_path,
                username=releaseConfig['hgUsername'],
                sshKey=releaseConfig['hgSshKey'],
                repositories=clone_repositories,
                clobberURL=branchConfig['base_clobber_url'],
                userRepoRoot=releaseConfig['userRepoRoot'],
            )

            builders.append({
                'name': builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName']),
                'slavenames': unix_slaves,
                'category': builderPrefix(''),
                'builddir': builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName']),
                'slavebuilddir': reallyShort(builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName'])),
                'factory': repository_setup_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': reallyShort(builderPrefix(
                        '%s_repo_setup' % releaseConfig['productName'])),
                    'release_config': releaseConfigFile,
                    },
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName']),
                slaves=all_slaves,
                category=builderPrefix(''),
                ))

        if not releaseConfig.get('skip_release_download'):
            release_downloader_factory = ScriptFactory(
                scriptRepo=tools_repo,
                extra_args=[branchConfigFile],
                scriptName='scripts/staging/release_downloader.sh',
            )

            builders.append({
                'name': builderPrefix(
                    '%s_release_downloader' % releaseConfig['productName']),
                'slavenames': unix_slaves,
                'category': builderPrefix(''),
                'builddir': builderPrefix(
                    '%s_release_downloader' % releaseConfig['productName']),
                'slavebuilddir': reallyShort(builderPrefix(
                    '%s_release_downloader' % releaseConfig['productName'])),
                'factory': release_downloader_factory,
                'env': builder_env,
                'properties': {
                    'release_config': releaseConfigFile,
                    'builddir': builderPrefix('%s_release_downloader' % \
                                              releaseConfig['productName']),
                    'slavebuilddir': reallyShort(builderPrefix(
                        '%s_release_downloader' % \
                        releaseConfig['productName']))
                }
            })

    if not releaseConfig.get('skip_tag'):
        pf = branchConfig['platforms']['linux']
        tag_env = builder_env.copy()
        if pf['env'].get('HG_SHARE_BASE_DIR', None):
            tag_env['HG_SHARE_BASE_DIR'] = pf['env']['HG_SHARE_BASE_DIR']

        tag_factory = ScriptFactory(
            scriptRepo=tools_repo,
            scriptName='scripts/release/tagging.sh',
        )

        builders.append({
            'name': builderPrefix('%s_tag' % releaseConfig['productName']),
            'slavenames': pf['slaves'] + \
            branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_tag' % releaseConfig['productName']),
            'slavebuilddir': reallyShort(
                builderPrefix('%s_tag' % releaseConfig['productName'])),
            'factory': tag_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': tag_env,
            'properties': {
                'builddir': builderPrefix(
                    '%s_tag' % releaseConfig['productName']),
                'slavebuilddir': reallyShort(
                    builderPrefix('%s_tag' % releaseConfig['productName'])),
                'release_config': releaseConfigFile,
            }
        })
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_tag' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            ))

    if not releaseConfig.get('skip_source'):
        pf = branchConfig['platforms']['linux']
        mozconfig = 'linux/%s/release' % sourceRepoInfo['name']
        source_factory = SingleSourceFactory(
            env=pf['env'],
            objdir=pf['platform_objdir'],
            hgHost=branchConfig['hghost'],
            buildToolsRepoPath=tools_repo_path,
            repoPath=sourceRepoInfo['path'],
            productName=releaseConfig['productName'],
            version=releaseConfig['version'],
            baseTag=releaseConfig['baseTag'],
            stagingServer=branchConfig['stage_server'],
            stageUsername=branchConfig['stage_username'],
            stageSshKey=branchConfig['stage_ssh_key'],
            buildNumber=releaseConfig['buildNumber'],
            autoconfDirs=['.', 'js/src'],
            clobberURL=branchConfig['base_clobber_url'],
            mozconfig=mozconfig,
            configRepoPath=branchConfig['config_repo_path'],
            configSubDir=branchConfig['config_subdir'],
            signingServers=signingServers,
            enableSigning=releaseConfig.get('enableSigningAtBuildTime', True),
        )

        builders.append({
           'name': builderPrefix('%s_source' % releaseConfig['productName']),
            'slavenames': branchConfig['platforms']['linux']['slaves'] + \
            branchConfig['platforms']['linux64']['slaves'],
           'category': builderPrefix(''),
           'builddir': builderPrefix(
               '%s_source' % releaseConfig['productName']),
           'slavebuilddir': reallyShort(
               builderPrefix('%s_source' % releaseConfig['productName'])),
           'factory': source_factory,
           'env': builder_env,
           'nextSlave': _nextFastReservedSlave,
           'properties': { 'slavebuilddir':
               reallyShort(
                   builderPrefix('%s_source' % releaseConfig['productName']))}
        })
        deliverables_builders.append(
                builderPrefix('%s_source' % releaseConfig['productName']))

        if releaseConfig.get('xulrunnerPlatforms'):
            mozconfig = 'linux/%s/xulrunner' % sourceRepoInfo['name']
            xulrunner_source_factory = SingleSourceFactory(
                env=pf['env'],
                objdir=pf['platform_objdir'],
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=tools_repo_path,
                repoPath=sourceRepoInfo['path'],
                productName='xulrunner',
                version=releaseConfig['version'],
                baseTag=releaseConfig['baseTag'],
                stagingServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username_xulrunner'],
                stageSshKey=branchConfig['stage_ssh_xulrunner_key'],
                buildNumber=releaseConfig['buildNumber'],
                autoconfDirs=['.', 'js/src'],
                clobberURL=branchConfig['base_clobber_url'],
                mozconfig=mozconfig,
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                signingServers=signingServers,
                enableSigning=False,
            )

            builders.append({
               'name': builderPrefix('xulrunner_source'),
               'slavenames': branchConfig['platforms']['linux']['slaves'] + \
               branchConfig['platforms']['linux64']['slaves'],
               'category': builderPrefix(''),
               'builddir': builderPrefix('xulrunner_source'),
               'slavebuilddir': reallyShort(builderPrefix('xulrunner_source')),
               'factory': xulrunner_source_factory,
               'env': builder_env,
               'properties': { 'slavebuilddir':
                   reallyShort(builderPrefix('xulrunner_source'))}
            })
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_source' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            ))
        if releaseConfig.get('xulrunnerPlatforms'):
            builders.append(makeDummyBuilder(
                name=builderPrefix('xulrunner_source'),
                slaves=all_slaves,
                category=builderPrefix(''),
                ))

    for platform in releaseConfig['enUSPlatforms']:
        # shorthand
        pf = branchConfig['platforms'][platform]
        mozconfig = '%s/%s/release' % (platform, sourceRepoInfo['name'])
        if platform in releaseConfig['talosTestPlatforms']:
            talosMasters = pf['talos_masters']
        else:
            talosMasters = None

        if releaseConfig['enableUnittests']:
            packageTests = True
            unittestMasters = branchConfig['unittest_masters']
            unittestBranch = builderPrefix('%s-opt-unittest' % platform)
        else:
            packageTests = False
            unittestMasters = None
            unittestBranch = None

        if not releaseConfig.get('skip_build'):
            platform_env = pf['env'].copy()
            if 'update_channel' in branchConfig:
                platform_env['MOZ_UPDATE_CHANNEL'] = branchConfig['update_channel']
            if platform in releaseConfig['l10nPlatforms']:
                triggeredSchedulers = [builderPrefix('%s_repack' % platform)]
            else:
                triggeredSchedulers = None
            multiLocaleConfig = releaseConfig.get(
                'mozharness_config', {}).get('platforms', {}).get(platform)
            mozharnessMultiOptions = releaseConfig.get(
                'mozharness_config', {}).get('multilocaleOptions')
            enableUpdatePackaging = bool(releaseConfig.get('verifyConfigs',
                                                      {}).get(platform))
            build_factory = ReleaseBuildFactory(
                env=platform_env,
                objdir=pf['platform_objdir'],
                platform=platform,
                hgHost=branchConfig['hghost'],
                repoPath=sourceRepoInfo['path'],
                buildToolsRepoPath=tools_repo_path,
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                profiledBuild=pf['profiled_build'],
                mozconfig=mozconfig,
                srcMozconfig=releaseConfig.get('mozconfigs', {}).get(platform),
                buildRevision=releaseTag,
                stageServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username'],
                stageGroup=branchConfig['stage_group'],
                stageSshKey=branchConfig['stage_ssh_key'],
                stageBasePath=branchConfig['stage_base_path'],
                codesighs=False,
                uploadPackages=True,
                uploadSymbols=True,
                createSnippet=False,
                doCleanup=True, # this will clean-up the mac build dirs, but not delete
                                # the entire thing
                buildSpace=10,
                productName=releaseConfig['productName'],
                version=releaseConfig['version'],
                buildNumber=releaseConfig['buildNumber'],
                oldVersion=releaseConfig['oldVersion'],
                oldBuildNumber=releaseConfig['oldBuildNumber'],
                talosMasters=talosMasters,
                packageTests=packageTests,
                unittestMasters=unittestMasters,
                unittestBranch=unittestBranch,
                clobberURL=branchConfig['base_clobber_url'],
                triggerBuilds=True,
                triggeredSchedulers=triggeredSchedulers,
                stagePlatform=buildbot2ftp(platform),
                use_scratchbox=pf.get('use_scratchbox'),
                android_signing=pf.get('android_signing', False),
                multiLocale=pf.get('multi_locale', False),
                multiLocaleMerge=releaseConfig.get('mergeLocales', False),
                compareLocalesRepoPath=branchConfig['compare_locales_repo_path'],
                mozharnessRepoPath=branchConfig['mozharness_repo_path'],
                mozharnessTag=releaseTag,
                multiLocaleScript=pf.get('multi_locale_script'),
                multiLocaleConfig=multiLocaleConfig,
                mozharnessMultiOptions=mozharnessMultiOptions,
                usePrettyNames=releaseConfig.get('usePrettyNames', True),
                enableUpdatePackaging=enableUpdatePackaging,
                mozconfigBranch=releaseTag,
                signingServers=signingServers,
                enableSigning=releaseConfig.get('enableSigningAtBuildTime', True),
                createPartial=releaseConfig.get('enablePartialMarsAtBuildTime', True),
            )

            builders.append({
                'name': builderPrefix('%s_build' % platform),
                'slavenames': pf['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('%s_build' % platform),
                'slavebuilddir': reallyShort(builderPrefix('%s_build' % platform)),
                'factory': build_factory,
                'nextSlave': _nextFastReservedSlave,
                'env': builder_env,
                'properties': { 'slavebuilddir':
                    reallyShort(builderPrefix('%s_build' % platform))}
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix('%s_build' % platform),
                slaves=all_slaves,
                category=builderPrefix(''),
                ))
        updates_upstream_builders.append(builderPrefix('%s_build' % platform))
        deliverables_builders.append(builderPrefix('%s_build' % platform))

        if platform in releaseConfig['l10nPlatforms']:
            env = builder_env.copy()
            env.update(pf['env'])
            if 'update_channel' in branchConfig:
                env['MOZ_UPDATE_CHANNEL'] = branchConfig['update_channel']

            if not releaseConfig.get('disableStandaloneRepacks'):
                extra_args = [platform, branchConfigFile]
                if releaseConfig.get('enablePartialMarsAtBuildTime', True):
                    extra_args.append('generatePartials')
                standalone_factory = SigningScriptFactory(
                    signingServers=signingServers,
                    env=env,
                    enableSigning=releaseConfig.get('enableSigningAtBuildTime', True),
                    scriptRepo=tools_repo,
                    interpreter='bash',
                    scriptName='scripts/l10n/standalone_repacks.sh',
                    extra_args=extra_args,
                )
                builders.append({
                    'name': builderPrefix("standalone_repack", platform),
                    'slavenames': branchConfig['l10n_slaves'][platform],
                    'category': builderPrefix(''),
                    'builddir': builderPrefix("standalone_repack", platform),
                    'factory': standalone_factory,
                    'nextSlave': _nextFastReservedSlave,
                    'env': env,
                    'properties': {'builddir':
                                   builderPrefix("standalone_repack", platform)}
                })

            for n, builderName in l10nBuilders(platform).iteritems():
                if releaseConfig['productName'] == 'fennec':
                    repack_factory = ScriptFactory(
                        scriptRepo=tools_repo,
                        interpreter='bash',
                        scriptName='scripts/l10n/release_mobile_repacks.sh',
                        extra_args=[platform, branchConfigFile,
                                    str(l10nChunks), str(n)]
                    )
                else:
                    extra_args = [platform, branchConfigFile, str(l10nChunks),
                                  str(n)]
                    if releaseConfig.get('enablePartialMarsAtBuildTime', True):
                        extra_args.append('generatePartials')
                    repack_factory = SigningScriptFactory(
                        signingServers=signingServers,
                        env=env,
                        enableSigning=releaseConfig.get('enableSigningAtBuildTime', True),
                        scriptRepo=tools_repo,
                        interpreter='bash',
                        scriptName='scripts/l10n/release_repacks.sh',
                        extra_args=extra_args,
                    )

                builddir = builderPrefix('%s_repack' % platform) + \
                                         '_' + str(n)
                builders.append({
                    'name': builderName,
                    'slavenames': branchConfig['l10n_slaves'][platform],
                    'category': builderPrefix(''),
                    'builddir': builddir,
                    'slavebuilddir': reallyShort(builddir),
                    'factory': repack_factory,
                    'nextSlave': _nextFastReservedSlave,
                    'env': env,
                    'properties': {
                        'builddir': builddir,
                        'slavebuilddir': reallyShort(builddir),
                        'release_config': releaseConfigFile,
                    }
                })

            builders.append(makeDummyBuilder(
                name=builderPrefix('repack_complete', platform),
                slaves=all_slaves,
                category=builderPrefix(''),
            ))
            updates_upstream_builders.append(
                builderPrefix('repack_complete', platform))
            deliverables_builders.append(
                builderPrefix('repack_complete', platform))

        if platform in releaseConfig['unittestPlatforms']:
            mochitestLeakThreshold = pf.get('mochitest_leak_threshold', None)
            crashtestLeakThreshold = pf.get('crashtest_leak_threshold', None)
            for suites_name, suites in branchConfig['unittest_suites']:
                # Release builds on mac don't have a11y enabled, do disable the mochitest-a11y test
                if platform.startswith('macosx') and 'mochitest-a11y' in suites:
                    suites = suites[:]
                    suites.remove('mochitest-a11y')

                test_builders.extend(generateTestBuilder(
                    branchConfig, 'release', platform, builderPrefix("%s_test" % platform),
                    builderPrefix("%s-opt-unittest" % platform),
                    suites_name, suites, mochitestLeakThreshold,
                    crashtestLeakThreshold, category=builderPrefix('')))

    for platform in releaseConfig.get('xulrunnerPlatforms', []):
        pf = branchConfig['platforms'][platform]
        xr_env = pf['env'].copy()
        xr_env['SYMBOL_SERVER_USER'] = branchConfig['stage_username_xulrunner']
        xr_env['SYMBOL_SERVER_PATH'] = branchConfig['symbol_server_xulrunner_path']
        xr_env['SYMBOL_SERVER_SSH_KEY'] = \
            xr_env['SYMBOL_SERVER_SSH_KEY'].replace(branchConfig['stage_ssh_key'],
                                                    branchConfig['stage_ssh_xulrunner_key'])
        if not releaseConfig.get('skip_build'):
            xulrunner_build_factory = XulrunnerReleaseBuildFactory(
                env=xr_env,
                objdir=pf['platform_objdir'],
                platform=platform,
                hgHost=branchConfig['hghost'],
                repoPath=sourceRepoInfo['path'],
                buildToolsRepoPath=tools_repo_path,
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                profiledBuild=None,
                mozconfig = '%s/%s/xulrunner' % (platform, sourceRepoInfo['name']),
                srcMozconfig=releaseConfig.get('xulrunner_mozconfigs', {}).get(platform),
                buildRevision=releaseTag,
                stageServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username_xulrunner'],
                stageGroup=branchConfig['stage_group'],
                stageSshKey=branchConfig['stage_ssh_xulrunner_key'],
                stageBasePath=branchConfig['stage_base_path'] + '/xulrunner',
                codesighs=False,
                uploadPackages=True,
                uploadSymbols=True,
                createSnippet=False,
                doCleanup=True, # this will clean-up the mac build dirs, but not delete
                                # the entire thing
                buildSpace=pf.get('build_space', branchConfig['default_build_space']),
                productName='xulrunner',
                version=releaseConfig['version'],
                buildNumber=releaseConfig['buildNumber'],
                clobberURL=branchConfig['base_clobber_url'],
                packageSDK=True,
                enableSigning=False,
            )
            builders.append({
                'name': builderPrefix('xulrunner_%s_build' % platform),
                'slavenames': pf['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('xulrunner_%s_build' % platform),
                'slavebuilddir': reallyShort(builderPrefix('xulrunner_%s_build' % platform)),
                'factory': xulrunner_build_factory,
                'env': builder_env,
                'properties': {'slavebuilddir':
                    reallyShort(builderPrefix('xulrunner_%s_build' % platform))}
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix('xulrunner_%s_build' % platform),
                slaves=all_slaves,
                category=builderPrefix(''),
                ))

    if releaseConfig['doPartnerRepacks']:
        for platform in releaseConfig.get('partnerRepackPlatforms',
                                          releaseConfig['l10nPlatforms']):
            repack_params = dict(
                hgHost=branchConfig['hghost'],
                repoPath=sourceRepoInfo['path'],
                buildToolsRepoPath=tools_repo_path,
                productName=releaseConfig['productName'],
                version=releaseConfig['version'],
                buildNumber=releaseConfig['buildNumber'],
                partnersRepoPath=releaseConfig['partnersRepoPath'],
                partnersRepoRevision=releaseTag,
                platformList=[platform],
                stagingServer=releaseConfig['stagingServer'],
                stageUsername=branchConfig['stage_username'],
                stageSshKey=branchConfig['stage_ssh_key'],
                signingServers=signingServers,
                enableSigning=releaseConfig.get('enableSigningAtBuildTime', True),
            )

            if 'macosx64' in branchConfig['platforms']:
                slaves = branchConfig['platforms']['macosx64']['slaves']
            else:
                slaves = branchConfig['platforms']['macosx']['slaves']

            if releaseConfig['productName'] == 'fennec':
                repack_params['productName'] = 'mobile'
                repack_params['platformList'] = [buildbot2ftp(platform)]
                repack_params['nightlyDir'] = 'candidates'
                repack_params['packageDmg'] = False
                slaves = branchConfig['platforms']['linux']['slaves']

            partner_repack_factory = PartnerRepackFactory(**repack_params)

            builders.append({
                'name': builderPrefix('partner_repack', platform),
                'slavenames': slaves,
                'category': builderPrefix(''),
                'builddir': builderPrefix('partner_repack', platform),
                'slavebuilddir': reallyShort(builderPrefix(
                    'partner_repack', platform)),
                'factory': partner_repack_factory,
                'nextSlave': _nextFastReservedSlave,
                'env': builder_env,
                'properties': {'slavebuilddir':
                               reallyShort(builderPrefix(
                                   'partner_repack', platform))}
            })
            deliverables_builders.append(
                builderPrefix('partner_repack', platform))

    if not releaseConfig.get('disableL10nVerification'):
        for platform in releaseConfig['l10nPlatforms']:
            l10n_verification_factory = L10nVerifyFactory(
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=tools_repo_path,
                cvsroot=releaseConfig['cvsroot'],
                stagingServer=releaseConfig['stagingServer'],
                productName=releaseConfig['productName'],
                version=releaseConfig['version'],
                buildNumber=releaseConfig['buildNumber'],
                oldVersion=releaseConfig['oldVersion'],
                oldBuildNumber=releaseConfig['oldBuildNumber'],
                clobberURL=branchConfig['base_clobber_url'],
                platform=platform,
            )

            if 'macosx64' in branchConfig['platforms']:
                slaves = branchConfig['platforms']['macosx64']['slaves']
            else:
                slaves = branchConfig['platforms']['macosx']['slaves']
            builders.append({
                'name': builderPrefix('l10n_verification', platform),
                'slavenames': slaves,
                'category': builderPrefix(''),
                'builddir': builderPrefix('l10n_verification', platform),
                'slavebuilddir': reallyShort(builderPrefix('l10n_verification', platform)),
                'factory': l10n_verification_factory,
                'nextSlave': _nextFastReservedSlave,
                'env': builder_env,
                'properties': {'slavebuilddir':reallyShort(
                    builderPrefix('l10n_verification', platform))}
            })
            post_signing_builders.append(
                builderPrefix('l10n_verification', platform))

    if not releaseConfig.get('enableSigningAtBuildTime', True) and \
       releaseConfig['productName'] == 'firefox':
        builders.append(makeDummyBuilder(
            name=builderPrefix('signing_done'),
            slaves=all_slaves,
            category=builderPrefix('')
        ))

    if releaseConfig.get('enableSigningAtBuildTime', True) and \
           releaseConfig['productName'] == 'firefox':
        pf = branchConfig['platforms']['linux']
        env = builder_env.copy()
        env.update(pf['env'])
        checksums_factory = SigningScriptFactory(
            signingServers=signingServers,
            env=env,
            scriptRepo=tools_repo,
            interpreter='bash',
            scriptName='scripts/release/generate-sums.sh',
            extra_args=[branchConfigFile],
        )
        builders.append({
            'name': builderPrefix('%s_checksums' % releaseConfig['productName']),
            'slavenames': branchConfig['platforms']['linux']['slaves'] + \
                branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix(
                '%s_checksums' % releaseConfig['productName']),
            'slavebuilddir': reallyShort(
                builderPrefix('%s_checksums' % releaseConfig['productName'])),
            'factory': checksums_factory,
            'env': builder_env,
            'properties': {
                'slavebuilddir': builderPrefix(
                    '%s_checksums' % releaseConfig['productName']),
                'script_repo_revision': releaseTag,
                'release_config': releaseConfigFile,
            }
        })
        post_deliverables_builders.append(
            builderPrefix('%s_checksums' % releaseConfig['productName']))

    if releaseConfig.get('verifyConfigs') and \
       not releaseConfig.get('skip_updates'):
        updates_factory = ReleaseUpdatesFactory(
            hgHost=branchConfig['hghost'],
            repoPath=sourceRepoInfo['path'],
            buildToolsRepoPath=tools_repo_path,
            cvsroot=releaseConfig['cvsroot'],
            patcherToolsTag=releaseConfig['patcherToolsTag'],
            patcherConfig=releaseConfig['patcherConfig'],
            verifyConfigs=releaseConfig['verifyConfigs'],
            appName=releaseConfig['appName'],
            productName=releaseConfig['productName'],
            version=releaseConfig['version'],
            appVersion=releaseConfig['appVersion'],
            baseTag=releaseConfig['baseTag'],
            buildNumber=releaseConfig['buildNumber'],
            oldVersion=releaseConfig['oldVersion'],
            oldAppVersion=releaseConfig['oldAppVersion'],
            oldBaseTag=releaseConfig['oldBaseTag'],
            oldBuildNumber=releaseConfig['oldBuildNumber'],
            ftpServer=releaseConfig['ftpServer'],
            bouncerServer=releaseConfig['bouncerServer'],
            stagingServer=releaseConfig['stagingServer'],
            useBetaChannel=releaseConfig['useBetaChannel'],
            stageUsername=branchConfig['stage_username'],
            stageSshKey=branchConfig['stage_ssh_key'],
            ausUser=releaseConfig['ausUser'],
            ausSshKey=releaseConfig['ausSshKey'],
            ausHost=branchConfig['aus2_host'],
            ausServerUrl=releaseConfig['ausServerUrl'],
            hgSshKey=releaseConfig['hgSshKey'],
            hgUsername=releaseConfig['hgUsername'],
            # We disable this on staging, because we don't have a CVS mirror to
            # commit to
            commitPatcherConfig=releaseConfig['commitPatcherConfig'],
            clobberURL=branchConfig['base_clobber_url'],
            oldRepoPath=sourceRepoInfo['path'],
            releaseNotesUrl=releaseConfig['releaseNotesUrl'],
            binaryName=releaseConfig['binaryName'],
            oldBinaryName=releaseConfig['oldBinaryName'],
            testOlderPartials=releaseConfig['testOlderPartials'],
            longVersion=releaseConfig.get('longVersion', None),
            oldLongVersion=releaseConfig.get('oldLongVersion', None),
            schema=releaseConfig.get('snippetSchema', None),
            useBetaChannelForRelease=releaseConfig.get('useBetaChannelForRelease', False),
            signingServers=signingServers,
            useChecksums=releaseConfig.get('enablePartialMarsAtBuildTime', True),
        )

        builders.append({
            'name': builderPrefix('updates'),
            'slavenames': branchConfig['platforms']['linux']['slaves'] + branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('updates'),
            'slavebuilddir': reallyShort(builderPrefix('updates')),
            'factory': updates_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': builder_env,
            'properties': {'slavebuilddir': reallyShort(builderPrefix('updates'))}
        })
        post_signing_builders.append(builderPrefix('updates'))
        important_builders.append(builderPrefix('updates'))
        if not releaseConfig.get('enableSigningAtBuildTime', True) or \
           not releaseConfig.get('enablePartialMarsAtBuildTime', True):
            deliverables_builders.append(builderPrefix('updates'))
    elif releaseConfig.get('verifyConfigs'):
        builders.append(makeDummyBuilder(
            name=builderPrefix('updates'),
            slaves=all_slaves,
            category=builderPrefix('')
        ))
        post_signing_builders.append(builderPrefix('updates'))
        important_builders.append(builderPrefix('updates'))


    for platform in sorted(releaseConfig.get('verifyConfigs', {}).keys()):
        for n, builderName in updateVerifyBuilders(platform).iteritems():
            uv_factory = ScriptFactory(
                scriptRepo=tools_repo,
                interpreter='bash',
                scriptName='scripts/release/updates/chunked-verify.sh',
                extra_args=[platform, 'verifyConfigs',
                            str(updateVerifyChunks), str(n)],
                log_eval_func=lambda c, s: regex_log_evaluator(c, s, update_verify_error)
            )

            builddir = builderPrefix('%s_update_verify' % platform) + \
                                     '_' + str(n)
            env = builder_env.copy()
            env.update(branchConfig['platforms'][platform]['env'])

            builders.append({
                'name': builderName,
                'slavenames': branchConfig['platforms'][platform]['slaves'],
                'category': builderPrefix(''),
                'builddir': builddir,
                'slavebuilddir': reallyShort(builddir),
                'factory': uv_factory,
                'nextSlave': _nextFastReservedSlave,
                'env': env,
                'properties': {'builddir': builddir,
                               'slavebuilddir': reallyShort(builddir),
                               'script_repo_revision': runtimeTag,
                               'release_tag': releaseTag,
                               'release_config': releaseConfigFile},
            })
            post_update_builders.append(builderName)

    if not releaseConfig.get('disablePermissionCheck'):
        check_permissions_factory = ScriptFactory(
            scriptRepo=tools_repo,
            extra_args=[branchConfigFile, 'permissions'],
            script_timeout=3*60*60,
            scriptName='scripts/release/push-to-mirrors.sh',
            log_eval_func=lambda c, s: regex_log_evaluator(
                c, s, permission_check_error),
        )

        builders.append({
            'name': builderPrefix('check_permissions'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('check_permissions'),
            'slavebuilddir': reallyShort(builderPrefix('chk_prms')),
            'factory': check_permissions_factory,
            'env': builder_env,
            'properties': {'slavebuilddir': reallyShort(builderPrefix('chk_prms')),
                           'script_repo_revision': releaseTag,
                           'release_config': releaseConfigFile},
        })
        post_deliverables_builders.append(builderPrefix('check_permissions'))

    if not releaseConfig.get('disableVirusCheck'):
        antivirus_factory = ScriptFactory(
            scriptRepo=tools_repo,
            extra_args=[branchConfigFile, 'antivirus'],
            script_timeout=3*60*60,
            scriptName='scripts/release/push-to-mirrors.sh',
        )

        builders.append({
            'name': builderPrefix('antivirus'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('antivirus'),
            'slavebuilddir': reallyShort(builderPrefix('av')),
            'factory': antivirus_factory,
            'env': builder_env,
            'properties': {'slavebuilddir': reallyShort(builderPrefix('av')),
                           'script_repo_revision': releaseTag,
                           'release_config': releaseConfigFile},
        })
        post_deliverables_builders.append(builderPrefix('antivirus'))

    if not releaseConfig.get('disablePushToMirrors'):
        push_to_mirrors_factory = ScriptFactory(
            scriptRepo=tools_repo,
            extra_args=[branchConfigFile, 'push'],
            script_timeout=3*60*60,
            scriptName='scripts/release/push-to-mirrors.sh',
        )

        push_to_mirrors_factory.addStep(Trigger(
            schedulerNames=[builderPrefix('ready-for-rel-test'),
                            builderPrefix('ready-for-release')],
            copy_properties=['script_repo_revision', 'release_config']
        ))


        builders.append({
            'name': builderPrefix('push_to_mirrors'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('push_to_mirrors'),
            'slavebuilddir': reallyShort(builderPrefix('psh_mrrrs')),
            'factory': push_to_mirrors_factory,
            'env': builder_env,
            'properties': {
                'slavebuilddir': reallyShort(builderPrefix('psh_mrrrs')),
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                },
        })

    for platform in releaseConfig.get('verifyConfigs', {}).keys():
        final_verification_factory = ReleaseFinalVerification(
            hgHost=branchConfig['hghost'],
            platforms=[platform],
            buildToolsRepoPath=tools_repo_path,
            verifyConfigs=releaseConfig['verifyConfigs'],
            clobberURL=branchConfig['base_clobber_url'],
        )

        builders.append({
            'name': builderPrefix('final_verification', platform),
            'slavenames': branchConfig['platforms']['linux']['slaves'] + \
            branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('final_verification', platform),
            'slavebuilddir': reallyShort(builderPrefix('fnl_verf', platform)),
            'factory': final_verification_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': builder_env,
            'properties': {'slavebuilddir':
                           reallyShort(builderPrefix('fnl_verf', platform))}
        })

    if not releaseConfig.get('disableBouncerEntries'):
        builders.append(makeDummyBuilder(
            name=builderPrefix('ready_for_releasetest_testing'),
            slaves=all_slaves,
            category=builderPrefix(''),
            ))
        important_builders.append(builderPrefix('ready_for_releasetest_testing'))

        builders.append(makeDummyBuilder(
            name=builderPrefix('ready_for_release'),
            slaves=all_slaves,
            category=builderPrefix(''),
            ))
        important_builders.append(builderPrefix('ready_for_release'))

    if releaseConfig.get('majorUpdateRepoPath'):
        # Not attached to any Scheduler
        major_update_factory = MajorUpdateFactory(
            hgHost=branchConfig['hghost'],
            repoPath=releaseConfig['majorUpdateRepoPath'],
            buildToolsRepoPath=tools_repo_path,
            cvsroot=releaseConfig['cvsroot'],
            patcherToolsTag=releaseConfig['majorPatcherToolsTag'],
            patcherConfig=releaseConfig['majorUpdatePatcherConfig'],
            verifyConfigs=releaseConfig['majorUpdateVerifyConfigs'],
            appName=releaseConfig['appName'],
            productName=releaseConfig['productName'],
            version=releaseConfig['majorUpdateToVersion'],
            appVersion=releaseConfig['majorUpdateAppVersion'],
            baseTag=releaseConfig['majorUpdateBaseTag'],
            buildNumber=releaseConfig['majorUpdateBuildNumber'],
            oldVersion=releaseConfig['version'],
            oldAppVersion=releaseConfig['appVersion'],
            oldBaseTag=releaseConfig['baseTag'],
            oldBuildNumber=releaseConfig['buildNumber'],
            ftpServer=releaseConfig['ftpServer'],
            bouncerServer=releaseConfig['bouncerServer'],
            stagingServer=releaseConfig['stagingServer'],
            useBetaChannel=releaseConfig['useBetaChannel'],
            stageUsername=branchConfig['stage_username'],
            stageSshKey=branchConfig['stage_ssh_key'],
            ausUser=releaseConfig['ausUser'],
            ausSshKey=releaseConfig['ausSshKey'],
            ausHost=branchConfig['aus2_host'],
            ausServerUrl=releaseConfig['ausServerUrl'],
            hgSshKey=releaseConfig['hgSshKey'],
            hgUsername=releaseConfig['hgUsername'],
            # We disable this on staging, because we don't have a CVS mirror to
            # commit to
            commitPatcherConfig=releaseConfig['commitPatcherConfig'],
            clobberURL=branchConfig['base_clobber_url'],
            oldRepoPath=sourceRepoInfo['path'],
            triggerSchedulers=[builderPrefix('major_update_verify')],
            releaseNotesUrl=releaseConfig['majorUpdateReleaseNotesUrl'],
            fakeMacInfoTxt=releaseConfig['majorFakeMacInfoTxt'],
            schema=releaseConfig.get('majorSnippetSchema', None),
            useBetaChannelForRelease=releaseConfig.get('useBetaChannelForRelease', True),
        )

        builders.append({
            'name': builderPrefix('major_update'),
            'slavenames': branchConfig['platforms']['linux']['slaves'] + branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('major_update'),
            'slavebuilddir': reallyShort(builderPrefix('mu')),
            'factory': major_update_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': builder_env,
            'properties': {'slavebuilddir': reallyShort(builderPrefix('mu'))}
        })
        important_builders.append(builderPrefix('major_update'))

        for platform in sorted(releaseConfig['majorUpdateVerifyConfigs'].keys()):
            for n, builderName in majorUpdateVerifyBuilders(platform).iteritems():
                muv_factory = ScriptFactory(
                    scriptRepo=tools_repo,
                    interpreter='bash',
                    scriptName='scripts/release/updates/chunked-verify.sh',
                    extra_args=[platform, 'majorUpdateVerifyConfigs',
                                str(updateVerifyChunks), str(n)],
                    log_eval_func=lambda c, s: regex_log_evaluator(c, s, update_verify_error)
                )

                builddir = builderPrefix('%s_major_update_verify' % platform) + \
                                        '_' + str(n)
                env = builder_env.copy()
                env.update(branchConfig['platforms'][platform]['env'])
                mu_runtimeTag = getRuntimeTag(getReleaseTag(
                    releaseConfig['majorUpdateBaseTag']))

                builders.append({
                    'name': builderName,
                    'slavenames': branchConfig['platforms'][platform]['slaves'],
                    'category': builderPrefix(''),
                    'builddir': builddir,
                    'slavebuilddir': reallyShort(builddir),
                    'factory': muv_factory,
                    'nextSlave': _nextFastReservedSlave,
                    'env': env,
                    'properties': {'builddir': builddir,
                                   'slavebuilddir': reallyShort(builddir),
                                   'script_repo_revision': mu_runtimeTag,
                                   'release_tag': releaseTag,
                                   'release_config': releaseConfigFile},
                })

    if not releaseConfig.get('disableBouncerEntries'):
        bouncer_submitter_factory = TuxedoEntrySubmitterFactory(
            baseTag=releaseConfig['baseTag'],
            appName=releaseConfig['appName'],
            config=releaseConfig['tuxedoConfig'],
            productName=releaseConfig['productName'],
            version=releaseConfig['version'],
            milestone=releaseConfig['milestone'],
            tuxedoServerUrl=releaseConfig['tuxedoServerUrl'],
            enUSPlatforms=releaseConfig['enUSPlatforms'],
            l10nPlatforms=releaseConfig['l10nPlatforms'],
            extraPlatforms=releaseConfig.get('extraBouncerPlatforms'),
            oldVersion=releaseConfig['oldVersion'],
            hgHost=branchConfig['hghost'],
            repoPath=sourceRepoInfo['path'],
            buildToolsRepoPath=tools_repo_path,
            credentialsFile=os.path.join(os.getcwd(), "BuildSlaves.py")
        )

        builders.append({
            'name': builderPrefix('bouncer_submitter'),
            'slavenames': branchConfig['platforms']['linux']['slaves'] + \
            branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('bouncer_submitter'),
            'slavebuilddir': reallyShort(builderPrefix('bncr_sub')),
            'factory': bouncer_submitter_factory,
            'env': builder_env,
            'properties': {
                'slavebuilddir': reallyShort(builderPrefix('bncr_sub')),
                'release_config': releaseConfigFile,
            }
        })

        if releaseConfig['doPartnerRepacks']:
            euballot_bouncer_submitter_factory = TuxedoEntrySubmitterFactory(
                baseTag=releaseConfig['baseTag'],
                appName=releaseConfig['appName'],
                config=releaseConfig['tuxedoConfig'],
                productName=releaseConfig['productName'],
                bouncerProductSuffix='EUballot',
                version=releaseConfig['version'],
                milestone=releaseConfig['milestone'],
                tuxedoServerUrl=releaseConfig['tuxedoServerUrl'],
                enUSPlatforms=('win32-EUballot',),
                l10nPlatforms=None, # not needed
                oldVersion=None, # no updates
                hgHost=branchConfig['hghost'],
                repoPath=sourceRepoInfo['path'],
                buildToolsRepoPath=tools_repo_path,
                credentialsFile=os.path.join(os.getcwd(), "BuildSlaves.py"),
            )

            builders.append({
                'name': builderPrefix('euballot_bouncer_submitter'),
                'slavenames': branchConfig['platforms']['linux']['slaves'] + \
                branchConfig['platforms']['linux64']['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('euballot_bouncer_submitter'),
                'slavebuilddir': reallyShort(builderPrefix('eu_bncr_sub')),
                'factory': euballot_bouncer_submitter_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': reallyShort(builderPrefix('eu_bncr_sub')),
                    'release_config': releaseConfigFile,
                }
            })

    if releaseConfig['productName'] == 'fennec':
        # TODO: remove android_signature_verification related parts when the
        # verification procedure moved to post singing scripts
        envJava = builder_env.copy()
        envJava['PATH'] = '/tools/jdk6/bin:%s' % envJava.get(
            'PATH', ':'.join(('/opt/local/bin', '/tools/python/bin',
                              '/tools/buildbot/bin', '/usr/kerberos/bin',
                              '/usr/local/bin', '/bin', '/usr/bin',
                              '/home/cltbld/bin')))
        signature_verification_factory = ScriptFactory(
            scriptRepo=tools_repo,
            scriptName='release/signing/verify-android-signature.sh',
            extra_args=['--tools-dir=scripts/', '--release',
                        WithProperties('--apk=%(who)s')]
        )
        builders.append({
            'name': builderPrefix('android_signature_verification'),
            'slavenames': branchConfig['platforms']['linux']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('android_verify_sig'),
            'factory': signature_verification_factory,
            'env': envJava,
            'properties': {
                'builddir': builderPrefix('android_verify_sig'),
                'slavebuilddir':
                reallyShort(builderPrefix('android_verify_sig')),
                },
        })


    ##### Change sources and Schedulers

    if not releaseConfig.get('enableSigningAtBuildTime', True) and \
       releaseConfig['productName'] == 'firefox':
        change_source.append(UrlPoller(
            branch=builderPrefix("post_signing"),
            url='%s/win32_signing_build%s.log' % (
                makeCandidatesDir(
                    releaseConfig['productName'],
                    releaseConfig['version'],
                    releaseConfig['buildNumber'],
                    protocol='http',
                    server=releaseConfig['ftpServer']),
                releaseConfig['buildNumber']),
            pollInterval=60*10,
        ))
        signing_done_scheduler = Scheduler(
            name=builderPrefix('signing_done'),
            treeStableTimer=0,
            branch=builderPrefix('post_signing'),
            builderNames=[builderPrefix('signing_done')]
        )
        schedulers.append(signing_done_scheduler)
        important_builders.append(builderPrefix('signing_done'))

    if releaseConfig['productName'] == 'fennec':
        locale = 'en-US'
        candidatesDir = makeCandidatesDir(
            releaseConfig['productName'],
            releaseConfig['version'],
            releaseConfig['buildNumber'],
            protocol='http',
            server=releaseConfig['ftpServer'])
        enUS_signed_apk_url = '%s%s/%s/%s-%s.%s.android-arm.apk' % \
            (candidatesDir,
             buildbot2ftp('linux-android'),
             locale, releaseConfig['productName'], releaseConfig['version'],
             locale)
        change_source.append(UrlPoller(
            branch=builderPrefix('android_post_signing'),
            url=enUS_signed_apk_url,
            pollInterval=60*10
        ))
        if branchConfig['platforms']['linux-android'].get('multi_locale'):
            locale = 'multi'
            signed_apk_url = '%s%s/%s/%s-%s.%s.android-arm.apk' % \
                           (candidatesDir,
                            buildbot2ftp('linux-android'),
                            locale,
                            releaseConfig['productName'],
                            releaseConfig['version'],
                            locale)
            change_source.append(UrlPoller(
                branch=builderPrefix('android_post_signing'),
                url=signed_apk_url,
                pollInterval=60*10
            ))

    reset_schedulers_scheduler = Scheduler(
        name=builderPrefix('%s_reset_schedulers' % releaseConfig['productName']),
        branch=sourceRepoInfo['path'],
        treeStableTimer=None,
        builderNames=[builderPrefix(
            '%s_reset_schedulers' % releaseConfig['productName'])],
        fileIsImportant=lambda c: changeContainsProduct(c,
                                    releaseConfig['productName'])
    )
    schedulers.append(reset_schedulers_scheduler)
    if releaseConfig.get('enable_repo_setup'):
        repo_setup_scheduler = Dependent(
            name=builderPrefix('%s_repo_setup' % releaseConfig['productName']),
            upstream=reset_schedulers_scheduler,
            builderNames=[builderPrefix(
                '%s_repo_setup' % releaseConfig['productName'])],
        )
        schedulers.append(repo_setup_scheduler)
        tag_scheduler = Dependent(
            name=builderPrefix('%s_tag' % releaseConfig['productName']),
            upstream=repo_setup_scheduler,
            builderNames=[builderPrefix(
                '%s_tag' % releaseConfig['productName'])],
        )
        if not releaseConfig.get('skip_release_download'):
            release_downloader_scheduler = Scheduler(
                name=builderPrefix(
                    '%s_release_downloader' % releaseConfig['productName']),
                branch=sourceRepoInfo['path'],
                treeStableTimer=None,
                builderNames=[builderPrefix(
                    '%s_release_downloader' % releaseConfig['productName'])],
                fileIsImportant=lambda c: changeContainsProduct(c,
                                            releaseConfig['productName'])
            )
            schedulers.append(release_downloader_scheduler)
    else:
        tag_scheduler = Dependent(
            name=builderPrefix('%s_tag' % releaseConfig['productName']),
            upstream=reset_schedulers_scheduler,
            builderNames=[builderPrefix(
                '%s_tag' % releaseConfig['productName'])],
        )
    schedulers.append(tag_scheduler)

    tag_downstream = [builderPrefix('%s_source' % releaseConfig['productName'])]

    if releaseConfig['buildNumber'] == 1 \
       and not releaseConfig.get('disableBouncerEntries'):
        tag_downstream.append(builderPrefix('bouncer_submitter'))

        if releaseConfig['doPartnerRepacks']:
            tag_downstream.append(builderPrefix('euballot_bouncer_submitter'))

    if releaseConfig.get('xulrunnerPlatforms'):
        tag_downstream.append(builderPrefix('xulrunner_source'))

    for platform in releaseConfig['enUSPlatforms']:
        tag_downstream.append(builderPrefix('%s_build' % platform))
        if platform in releaseConfig['notifyPlatforms']:
            important_builders.append(builderPrefix('%s_build' % platform))
        if platform in releaseConfig['l10nPlatforms']:
            repack_scheduler = Triggerable(
                name=builderPrefix('%s_repack' % platform),
                builderNames=l10nBuilders(platform).values(),
            )
            schedulers.append(repack_scheduler)
            repack_complete_scheduler = Dependent(
                name=builderPrefix('%s_repack_complete' % platform),
                upstream=repack_scheduler,
                builderNames=[builderPrefix('repack_complete', platform),]
            )
            schedulers.append(repack_complete_scheduler)

    for platform in releaseConfig.get('xulrunnerPlatforms', []):
        tag_downstream.append(builderPrefix('xulrunner_%s_build' % platform))

    DependentID = makePropertiesScheduler(Dependent, [buildIDSchedFunc, buildUIDSchedFunc])

    schedulers.append(
        DependentID(
            name=builderPrefix('%s_build' % releaseConfig['productName']),
            upstream=tag_scheduler,
            builderNames=tag_downstream,
        ))

    if releaseConfig.get('majorUpdateRepoPath'):
        majorUpdateBuilderNames = []
        for platform in sorted(releaseConfig['majorUpdateVerifyConfigs'].keys()):
            majorUpdateBuilderNames.extend(
                majorUpdateVerifyBuilders(platform).values())
        major_update_verify_scheduler = Triggerable(
            name=builderPrefix('major_update_verify'),
            builderNames=majorUpdateBuilderNames
        )
        schedulers.append(major_update_verify_scheduler)

    for platform in releaseConfig['unittestPlatforms']:
        platform_test_builders = []
        for suites_name, suites in branchConfig['unittest_suites']:
            platform_test_builders.extend(
                    generateTestBuilderNames(
                        builderPrefix('%s_test' % platform),
                        suites_name, suites))

        s = Scheduler(
         name=builderPrefix('%s-opt-unittest' % platform),
         treeStableTimer=0,
         branch=builderPrefix('%s-opt-unittest' % platform),
         builderNames=platform_test_builders,
        )
        schedulers.append(s)

    if not releaseConfig.get('disableBouncerEntries'):
        mirror_scheduler1 = TriggerBouncerCheck(
            name=builderPrefix('ready-for-rel-test'),
            configRepo=config_repo,
            minUptake=releaseConfig.get('releasetestUptake', 3),
            builderNames=[builderPrefix('ready_for_releasetest_testing')] + \
                          [builderPrefix('final_verification', platform)
                           for platform in releaseConfig.get('verifyConfigs', {}).keys()],
            username=BuildSlaves.tuxedoUsername,
            password=BuildSlaves.tuxedoPassword)

        schedulers.append(mirror_scheduler1)

        mirror_scheduler2 = TriggerBouncerCheck(
            name=builderPrefix('ready-for-release'),
            configRepo=config_repo,
            minUptake=releaseConfig.get('releaseUptake', 45000),
            builderNames=[builderPrefix('ready_for_release')],
            username=BuildSlaves.tuxedoUsername,
            password=BuildSlaves.tuxedoPassword)

        schedulers.append(mirror_scheduler2)

    if releaseConfig['productName'] == 'fennec':
        android_signature_verification_scheduler = Scheduler(
            name=builderPrefix('android_signature_verification_scheduler'),
            branch=builderPrefix('android_post_signing'),
            treeStableTimer=None,
            builderNames=[builderPrefix('android_signature_verification')],
        )
        schedulers.append(android_signature_verification_scheduler)

    if releaseConfig.get('enableAutomaticPushToMirrors') and \
        releaseConfig.get('verifyConfigs'):
        if releaseConfig.get('disableVirusCheck'):
            post_update_builders.append(builderPrefix('push_to_mirrors'))
        else:
            post_antivirus_builders.append(builderPrefix('push_to_mirrors'))

    if releaseConfig['productName'] == 'firefox':
        if not releaseConfig.get('enableSigningAtBuildTime', True):
            updates_upstream_builders = [builderPrefix('signing_done')]
        schedulers.append(AggregatingScheduler(
            name=builderPrefix('%s_signing_done' % releaseConfig['productName']),
            branch=builderPrefix('%s_signing_done' % releaseConfig['productName']),
            upstreamBuilders=updates_upstream_builders,
            builderNames=post_signing_builders,
        ))
    if releaseConfig.get('verifyConfigs'):
        schedulers.append(AggregatingScheduler(
            name=builderPrefix('updates_done'),
            branch=builderPrefix('updates_done'),
            upstreamBuilders=[builderPrefix('updates')],
            builderNames=post_update_builders,
        ))
    if post_deliverables_builders:
        schedulers.append(AggregatingScheduler(
            name=builderPrefix('%s_deliverables_ready' % releaseConfig['productName']),
            branch=builderPrefix('%s_deliverables_ready' % releaseConfig['productName']),
            upstreamBuilders=deliverables_builders,
            builderNames=post_deliverables_builders,
        ))
    if post_antivirus_builders:
        schedulers.append(AggregatingScheduler(
            name=builderPrefix('av_done'),
            branch=builderPrefix('av_done'),
            upstreamBuilders=[builderPrefix('antivirus')],
            builderNames=post_antivirus_builders,
        ))
    if releaseConfig['doPartnerRepacks'] and \
        releaseConfig['productName'] == 'firefox':
        # TODO: revisit this once we have android partner repacks
        for platform in releaseConfig.get('partnerRepackPlatforms',
                                          releaseConfig['l10nPlatforms']):
            schedulers.append(AggregatingScheduler(
                name=builderPrefix('%s_l10n_done' % releaseConfig['productName'],
                                   platform),
                branch=builderPrefix('%s_l10n_done' % releaseConfig['productName'],
                                     platform),
                upstreamBuilders=[builderPrefix('repack_complete', platform)],
                builderNames=[builderPrefix('partner_repack', platform)],
            ))

    # This builder should be come after all AggregatingSchedulers are set
    aggregating_shedulers = []
    for s in schedulers:
        if isinstance(s, AggregatingScheduler):
            aggregating_shedulers.append(s.name)
    if aggregating_shedulers:
        scheduler_reset_factory = BuildFactory()
        scheduler_reset_factory.addStep(Trigger(
            schedulerNames=aggregating_shedulers,
        ))
        builders.append({
            'name': builderPrefix(
                '%s_reset_schedulers' % releaseConfig['productName']),
            'slavenames': all_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix(
                '%s_reset_schedulers' % releaseConfig['productName']),
            'factory': scheduler_reset_factory,
        })
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix(
                '%s_reset_schedulers' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
        ))

    # Separate email messages per list. Mailman doesn't try to avoid duplicate
    # messages in this case. See Bug 635527 for the details.
    tagging_started_recipients = releaseConfig['AllRecipients'][:]
    if not releaseConfig.get('skip_tag'):
        tagging_started_recipients.extend(releaseConfig['ImportantRecipients'])
    for recipient in tagging_started_recipients:
        #send a message when we receive the sendchange and start tagging
        status.append(ChangeNotifier(
                fromaddr="release@mozilla.com",
                relayhost="mail.build.mozilla.org",
                sendToInterestedUsers=False,
                extraRecipients=[recipient],
                branches=[sourceRepoInfo['path']],
                messageFormatter=createReleaseChangeMessage,
                changeIsImportant=lambda c: \
                changeContainsProduct(c, releaseConfig['productName'])
            ))
    for recipient in releaseConfig['AllRecipients']:
        if releaseConfig['productName'] == 'fennec':
            #send a message when android signing is complete
            status.append(ChangeNotifier(
                    fromaddr="release@mozilla.com",
                    relayhost="mail.build.mozilla.org",
                    sendToInterestedUsers=False,
                    extraRecipients=[recipient],
                    branches=[builderPrefix('android_post_signing')],
                    messageFormatter=createReleaseChangeMessage,
                    changeIsImportant=lambda c: \
                    changeContainsProperties(c, dict(who=enUS_signed_apk_url))
                ))

    #send the nice(passing) release messages
    status.append(MailNotifier(
            fromaddr='release@mozilla.com',
            sendToInterestedUsers=False,
            extraRecipients=releaseConfig['ImportantRecipients'],
            mode='passing',
            builders=important_builders,
            relayhost='mail.build.mozilla.org',
            messageFormatter=createReleaseMessage,
        ))

    #send all release messages
    status.append(MailNotifier(
            fromaddr='release@mozilla.com',
            sendToInterestedUsers=False,
            extraRecipients=releaseConfig['AllRecipients'],
            mode='all',
            builders=[b['name'] for b in builders + test_builders],
            relayhost='mail.build.mozilla.org',
            messageFormatter=createReleaseMessage,
        ))

    if releaseConfig.get('AVVendorsRecipients'):
        status.append(MailNotifier(
                fromaddr='release@mozilla.com',
                sendToInterestedUsers=False,
                extraRecipients=releaseConfig['AVVendorsRecipients'],
                mode='passing',
                builders=[builderPrefix('updates')],
                relayhost='mail.build.mozilla.org',
                messageFormatter=createReleaseAVVendorsMessage,
            ))

    if not releaseConfig.get('disable_tinderbox_mail'):
        status.append(TinderboxMailNotifier(
            fromaddr="mozilla2.buildbot@build.mozilla.org",
            tree=branchConfig["tinderbox_tree"] + "-Release",
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org",],
            relayhost="mail.build.mozilla.org",
            builders=[b['name'] for b in builders],
            logCompression="gzip")
        )

        status.append(TinderboxMailNotifier(
            fromaddr="mozilla2.buildbot@build.mozilla.org",
            tree=branchConfig["tinderbox_tree"] + "-Release",
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org",],
            relayhost="mail.build.mozilla.org",
            builders=[b['name'] for b in test_builders],
            logCompression="gzip",
            errorparser="unittest")
        )

    builders.extend(test_builders)

    product = releaseConfig['productName']
    if product == 'fennec':
        product = 'mobile'
    logUploadCmd = makeLogUploadCommand(sourceRepoInfo['name'], branchConfig,
            platform_prop=None, product=product)

    status.append(QueuedCommandHandler(
        logUploadCmd + [
            '--release', '%s/%s' % (
                releaseConfig['version'], releaseConfig['buildNumber'])
            ],
        QueueDir.getQueue('commands'),
        builders=[b['name'] for b in builders + test_builders],
    ))

    # Don't merge release builder requests
    nomergeBuilders.extend([b['name'] for b in builders + test_builders])

    return {
            "builders": builders,
            "status": status,
            "change_source": change_source,
            "schedulers": schedulers,
            }
