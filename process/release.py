# The absolute_import directive looks firstly at what packages are available
# on sys.path to avoid name collisions when we import release.* from elsewhere
from __future__ import absolute_import

import os
from buildbot.scheduler import Scheduler, Dependent, Triggerable
from buildbot.status.tinderbox import TinderboxMailNotifier
from buildbot.status.mail import MailNotifier
from buildbot.process.factory import BuildFactory
from buildbot.process.properties import WithProperties

from buildbotcustom.l10n import DependentL10n
from buildbotcustom.status.mail import ChangeNotifier
from buildbotcustom.misc import get_l10n_repositories, isHgPollerTriggered, \
  generateTestBuilderNames, generateTestBuilder, _nextFastReservedSlave
from buildbotcustom.process.factory import StagingRepositorySetupFactory, \
  ScriptFactory, SingleSourceFactory, ReleaseBuildFactory, \
  ReleaseUpdatesFactory, UpdateVerifyFactory, ReleaseFinalVerification, \
  L10nVerifyFactory, \
  PartnerRepackFactory, MajorUpdateFactory, XulrunnerReleaseBuildFactory, \
  TuxedoEntrySubmitterFactory, makeDummyBuilder
from buildbotcustom.changes.ftppoller import FtpPoller, LocalesFtpPoller
from release.platforms import ftp_platform_map, sl_platform_map

DEFAULT_L10N_CHUNKS = 15

def generateReleaseBranchObjects(releaseConfig, branchConfig, staging):
    l10nChunks = releaseConfig.get('l10nChunks', DEFAULT_L10N_CHUNKS)
    tools_repo = '%s%s' % (branchConfig['hgurl'],
                           branchConfig['build_tools_repo_path'])
    if staging:
        branchConfigFile = "mozilla/staging_config.py"
    else:
        branchConfigFile = "mozilla/production_config.py"

    def builderPrefix(s, platform=None):
        if platform:
            return "release-%s-%s_%s" % (releaseConfig['sourceRepoName'], platform, s)
        else:
            return "release-%s-%s" % (releaseConfig['sourceRepoName'], s)

    def releasePrefix():
        """Construct a standard format product release name from the
           product name, version and build number stored in release_config.py
        """
        return "%s %s build%s" % (
            releaseConfig['productName'].title(),
            releaseConfig['version'],
            releaseConfig['buildNumber'], )

    def createReleaseMessage(mode, name, build, results, master_status):
        """Construct a standard email to send to release@/release-drivers@
           whenever a major step of the release finishes
        """
        msgdict = {}
        releaseName = releasePrefix()
        job_status = "failed" if results else "success"
        allplatforms = list(releaseConfig['enUSPlatforms'])
        allplatforms.extend(["xulrunner_%s" % p for p in releaseConfig['xulrunnerPlatforms']])
        stage = name.replace(builderPrefix(""), "")
        platform = [p for p in allplatforms if stage.startswith(p)]
        platform = platform[0] if len(platform) >= 1 else None

        stage = stage.replace("%s_" % platform, "") if platform else stage
        #try to load a unique message template for the platform(if defined, step and results
        #if none exists, fall back to the default template
        possible_templates = ("%s/%s_%s_%s" % (releaseConfig['releaseTemplates'], platform, stage, job_status),
            "%s/%s_%s" % (releaseConfig['releaseTemplates'], stage, job_status),
            "%s/%s_default_%s" % (releaseConfig['releaseTemplates'], platform, job_status),
            "%s/default_%s" % (releaseConfig['releaseTemplates'], job_status))
        for t in possible_templates:
            if os.access(t, os.R_OK):
                template = open(t, "r", True)
                break

        if template:
            subject = template.readline().strip() % locals()
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
        step = None
        if change.branch.endswith('signing'):
            step = "signing"
        else:
            step = "tag"
        #try to load a unique message template for the change
        #if none exists, fall back to the default template
        possible_templates = ("%s/%s_change" % (releaseConfig['releaseTemplates'], step),
            "%s/default_change" % releaseConfig['releaseTemplates'])
        for t in possible_templates:
            if os.access(t, os.R_OK):
                template = open(t, "r", True)
                break

        if template:
            subject = template.readline().strip() % locals()
            body = ''.join(template.readlines()) + "\n"
            template.close()
        else:
            raise IOError("Cannot find a template file to use")
        msgdict['subject'] = subject % locals()
        msgdict['body'] = body % locals()
        msgdict['type'] = 'plain'
        return msgdict

    def l10nBuilders(platform):
        builders = {}
        for n in range(1, l10nChunks+1):
            builders[n] = builderPrefix("repack_%s/%s" % (n, str(l10nChunks)),
                                        platform)
        return builders

    builders = []
    test_builders = []
    schedulers = []
    change_source = []
    notify_builders = []
    status = []

    shippedLocalesFile = "%s/%s/raw-file/%s_RELEASE/%s" % (
                            branchConfig['hgurl'],
                            releaseConfig['sourceRepoPath'],
                            releaseConfig['baseTag'],
                            releaseConfig['shippedLocalesPath'])

    ##### Change sources and Schedulers
    for p in releaseConfig['l10nPlatforms']:
        ftpPlatform = ftp_platform_map[p]

        ftpURLs = ["http://%s/pub/mozilla.org/%s/nightly/%s-candidates/build%s/%s" % (
                  releaseConfig['stagingServer'],
                  releaseConfig['productName'],
                  releaseConfig['version'],
                  releaseConfig['buildNumber'],
                  ftpPlatform)]

        if p in ('win32', ):
            ftpURLs.append(
                "http://%s/pub/mozilla.org/%s/nightly/%s-candidates/build%s/unsigned/%s" % (
                  releaseConfig['stagingServer'],
                  releaseConfig['productName'],
                  releaseConfig['version'],
                  releaseConfig['buildNumber'],
                  ftpPlatform))

        change_source.append(LocalesFtpPoller(
            branch=builderPrefix("post_%s_l10n" % p),
            ftpURLs=ftpURLs,
            pollInterval=60*5, # 5 minutes
            platform = p,
            localesFile = shippedLocalesFile,
            sl_platform_map = sl_platform_map,
        ))

    change_source.append(FtpPoller(
        branch=builderPrefix("post_signing"),
        ftpURLs=[
            "http://%s/pub/mozilla.org/%s/nightly/%s-candidates/build%s/" % (
                   releaseConfig['stagingServer'],
                   releaseConfig['productName'], releaseConfig['version'],
                   releaseConfig['buildNumber'])],
        pollInterval= 60*10,
        searchString='win32_signing_build'
    ))

    if staging:
        repo_setup_scheduler = Scheduler(
            name=builderPrefix('repo_setup'),
            branch=releaseConfig['sourceRepoPath'],
            treeStableTimer=None,
            builderNames=[builderPrefix('repo_setup')],
            fileIsImportant=lambda c: not isHgPollerTriggered(c,
                branchConfig['hgurl'])
        )
        schedulers.append(repo_setup_scheduler)
        tag_scheduler = Dependent(
            name=builderPrefix('tag'),
            upstream=repo_setup_scheduler,
            builderNames=[builderPrefix('tag')]
        )
    else:
        tag_scheduler = Scheduler(
            name=builderPrefix('tag'),
            branch=releaseConfig['sourceRepoPath'],
            treeStableTimer=None,
            builderNames=[builderPrefix('tag')],
            fileIsImportant=lambda c: not isHgPollerTriggered(c, branchConfig['hgurl'])
        )

    schedulers.append(tag_scheduler)
    notify_builders.append(builderPrefix('tag'))
    source_scheduler = Dependent(
        name=builderPrefix('source'),
        upstream=tag_scheduler,
        builderNames=[builderPrefix('source')]
    )
    schedulers.append(source_scheduler)

    if releaseConfig['xulrunnerPlatforms']:
        xulrunner_source_scheduler = Dependent(
            name=builderPrefix('xulrunner_source'),
            upstream=tag_scheduler,
            builderNames=[builderPrefix('xulrunner_source')]
        )
        schedulers.append(xulrunner_source_scheduler)

    for platform in releaseConfig['enUSPlatforms']:
        build_scheduler = Dependent(
            name=builderPrefix('%s_build' % platform),
            upstream=tag_scheduler,
            builderNames=[builderPrefix('%s_build' % platform)]
        )
        schedulers.append(build_scheduler)
        notify_builders.append(builderPrefix('%s_build' % platform))
        if platform in releaseConfig['l10nPlatforms']:
            repack_scheduler = Dependent(
                name=builderPrefix('%s_repack' % platform),
                upstream=build_scheduler,
                builderNames=l10nBuilders(platform).values(),
            )
            schedulers.append(repack_scheduler)

    for platform in releaseConfig['xulrunnerPlatforms']:
        xulrunner_build_scheduler = Dependent(
            name=builderPrefix('xulrunner_%s_build' % platform),
            upstream=tag_scheduler,
            builderNames=[builderPrefix('xulrunner_%s_build' % platform)]
        )
        schedulers.append(xulrunner_build_scheduler)

    if releaseConfig['doPartnerRepacks']:
        for platform in releaseConfig['l10nPlatforms']:
            partner_scheduler = Scheduler(
                name=builderPrefix('partner_repacks', platform),
                treeStableTimer=0,
                branch=builderPrefix('post_%s_l10n' % platform),
                builderNames=[builderPrefix('partner_repack', platform)],
            )
            schedulers.append(partner_scheduler)

    for platform in releaseConfig['l10nPlatforms']:
        l10n_verify_scheduler = Scheduler(
            name=builderPrefix('l10n_verification', platform),
            treeStableTimer=0,
            branch=builderPrefix('post_signing'),
            builderNames=[builderPrefix('l10n_verification', platform)]
        )
        schedulers.append(l10n_verify_scheduler)
        notify_builders.append(builderPrefix('l10n_verification', platform))

    updates_scheduler = Scheduler(
        name=builderPrefix('updates'),
        treeStableTimer=0,
        branch=builderPrefix('post_signing'),
        builderNames=[builderPrefix('updates')]
    )
    schedulers.append(updates_scheduler)
    notify_builders.append(builderPrefix('updates'))

    updateBuilderNames = []
    for platform in sorted(releaseConfig['verifyConfigs'].keys()):
        updateBuilderNames.append(builderPrefix('%s_update_verify' % platform))
    update_verify_scheduler = Dependent(
        name=builderPrefix('update_verify'),
        upstream=updates_scheduler,
        builderNames=updateBuilderNames
    )
    schedulers.append(update_verify_scheduler)
    notify_builders.extend(updateBuilderNames)

    if releaseConfig['majorUpdateRepoPath']:
        majorUpdateBuilderNames = []
        for platform in sorted(releaseConfig['majorUpdateVerifyConfigs'].keys()):
            majorUpdateBuilderNames.append(
                    builderPrefix('%s_major_update_verify' % platform))
        major_update_verify_scheduler = Triggerable(
            name=builderPrefix('major_update_verify'),
            builderNames=majorUpdateBuilderNames
        )
        schedulers.append(major_update_verify_scheduler)

    for platform in releaseConfig['unittestPlatforms']:
        platform_test_builders = []
        base_name = branchConfig['platforms'][platform]['base_name']
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

    # Purposely, there is not a Scheduler for ReleaseFinalVerification
    # This is a step run very shortly before release, and is triggered manually
    # from the waterfall

    ##### Builders
    if staging:
        clone_repositories = {
            releaseConfig['sourceRepoClonePath']: {
                'revision': releaseConfig['sourceRepoRevision'],
                'relbranchOverride': releaseConfig['relbranchOverride'],
                'bumpFiles': ['config/milestone.txt',
                              'js/src/config/milestone.txt',
                              'browser/config/version.txt']
            }
        }
        if len(releaseConfig['l10nPlatforms']) > 0:
            l10n_clone_repos = get_l10n_repositories(
                releaseConfig['l10nRevisionFile'],
                releaseConfig['l10nRepoClonePath'],
                releaseConfig['relbranchOverride'])
            clone_repositories.update(l10n_clone_repos)

    builder_env = {
        'BUILDBOT_CONFIGS': '%s%s' % (branchConfig['hgurl'],
                                      branchConfig['config_repo_path']),
        'BUILDBOTCUSTOM': '%s%s' % (branchConfig['hgurl'],
                                    branchConfig['buildbotcustom_repo_path']),
        'CLOBBERER_URL': branchConfig['base_clobber_url']
    }

    if staging:
        if not releaseConfig.get('skip_repo_setup'):
            repository_setup_factory = StagingRepositorySetupFactory(
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=branchConfig['build_tools_repo_path'],
                username=releaseConfig['hgUsername'],
                sshKey=releaseConfig['hgSshKey'],
                repositories=clone_repositories,
                clobberURL=branchConfig['base_clobber_url'],
            )

            builders.append({
                'name': builderPrefix('repo_setup'),
                'slavenames': branchConfig['platforms']['linux']['slaves'],
                'category': 'release',
                'builddir': builderPrefix('repo_setup'),
                'factory': repository_setup_factory,
                'nextSlave': _nextFastReservedSlave,
                'env': builder_env,
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix('repo_setup'),
                slaves=branchConfig['platforms']['linux']['slaves'],
                category='release',
                ))

    if not releaseConfig.get('skip_tag'):
        tag_factory = ScriptFactory(
            scriptRepo=tools_repo,
            scriptName='scripts/release/tagging.sh',
        )

        builders.append({
            'name': builderPrefix('tag'),
            'slavenames': branchConfig['platforms']['linux']['slaves'],
            'category': 'release',
            'builddir': builderPrefix('tag'),
            'factory': tag_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': builder_env,
            'properties': {'builddir': builderPrefix('tag')}
        })
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix('tag'),
            slaves=branchConfig['platforms']['linux']['slaves'],
            category='release',
            ))

    if not releaseConfig.get('skip_source'):
        source_factory = SingleSourceFactory(
            hgHost=branchConfig['hghost'],
            buildToolsRepoPath=branchConfig['build_tools_repo_path'],
            repoPath=releaseConfig['sourceRepoPath'],
            productName=releaseConfig['productName'],
            version=releaseConfig['version'],
            baseTag=releaseConfig['baseTag'],
            stagingServer=branchConfig['stage_server'],
            stageUsername=branchConfig['stage_username'],
            stageSshKey=branchConfig['stage_ssh_key'],
            buildNumber=releaseConfig['buildNumber'],
            autoconfDirs=['.', 'js/src'],
            clobberURL=branchConfig['base_clobber_url'],
        )

        builders.append({
           'name': builderPrefix('source'),
           'slavenames': branchConfig['platforms']['linux']['slaves'],
           'category': 'release',
           'builddir': builderPrefix('source'),
           'factory': source_factory,
           'env': builder_env,
           'nextSlave': _nextFastReservedSlave,
        })

        if releaseConfig['xulrunnerPlatforms']:
            xulrunner_source_factory = SingleSourceFactory(
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=branchConfig['build_tools_repo_path'],
                repoPath=releaseConfig['sourceRepoPath'],
                productName='xulrunner',
                version=releaseConfig['milestone'],
                baseTag=releaseConfig['baseTag'],
                stagingServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username_xulrunner'],
                stageSshKey=branchConfig['stage_ssh_xulrunner_key'],
                buildNumber=releaseConfig['buildNumber'],
                autoconfDirs=['.', 'js/src'],
                clobberURL=branchConfig['base_clobber_url'],
            )

            builders.append({
               'name': builderPrefix('xulrunner_source'),
               'slavenames': branchConfig['platforms']['linux']['slaves'],
               'category': 'release',
               'builddir': builderPrefix('xulrunner_source'),
               'factory': xulrunner_source_factory,
               'env': builder_env,
            })
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix('source'),
            slaves=branchConfig['platforms']['linux']['slaves'],
            category='release',
            ))
        if releaseConfig['xulrunnerPlatforms']:
            builders.append(makeDummyBuilder(
                name=builderPrefix('xulrunner_source'),
                slaves=branchConfig['platforms']['linux']['slaves'],
                category='release',
                ))

    for platform in releaseConfig['enUSPlatforms']:
        # shorthand
        pf = branchConfig['platforms'][platform]
        mozconfig = '%s/%s/release' % (platform, releaseConfig['sourceRepoName'])
        if platform in releaseConfig['talosTestPlatforms']:
            talosMasters = branchConfig['talos_masters']
        else:
            talosMasters = None

        if platform in releaseConfig['unittestPlatforms']:
            packageTests = True
            unittestMasters = branchConfig['unittest_masters']
            unittestBranch = builderPrefix('%s-opt-unittest' % platform)
        else:
            packageTests = False
            unittestMasters = None
            unittestBranch = None

        if not releaseConfig.get('skip_build'):
            build_factory = ReleaseBuildFactory(
                env=pf['env'],
                objdir=pf['platform_objdir'],
                platform=platform,
                hgHost=branchConfig['hghost'],
                repoPath=releaseConfig['sourceRepoPath'],
                buildToolsRepoPath=branchConfig['build_tools_repo_path'],
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                profiledBuild=pf['profiled_build'],
                mozconfig=mozconfig,
                buildRevision='%s_RELEASE' % releaseConfig['baseTag'],
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
                talosMasters=talosMasters,
                packageTests=packageTests,
                unittestMasters=unittestMasters,
                unittestBranch=unittestBranch,
                clobberURL=branchConfig['base_clobber_url'],
            )

            builders.append({
                'name': builderPrefix('%s_build' % platform),
                'slavenames': pf['slaves'],
                'category': 'release',
                'builddir': builderPrefix('%s_build' % platform),
                'factory': build_factory,
                'nextSlave': _nextFastReservedSlave,
                'env': builder_env,
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix('%s_build' % platform),
                slaves=branchConfig['platforms']['linux']['slaves'],
                category='release',
                ))

        if platform in releaseConfig['l10nPlatforms']:
            for n, builderName in l10nBuilders(platform).iteritems():
                repack_factory = ScriptFactory(
                    scriptRepo=tools_repo,
                    interpreter='bash',
                    scriptName='scripts/l10n/release_repacks.sh',
                    extra_args=[platform, branchConfigFile,
                                str(l10nChunks), str(n)]
                )
                
                builddir = builderPrefix('%s_repack' % platform) + \
                                         '_' + str(n)
                env = builder_env.copy()
                env.update(pf['env'])
                builders.append({
                    'name': builderName,
                    'slavenames': branchConfig['l10n_slaves'][platform],
                    'category': 'release',
                    'builddir': builddir,
                    'factory': repack_factory,
                    'nextSlave': _nextFastReservedSlave,
                    'env': env,
                    'properties': {'builddir': builddir}
                })

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
                    crashtestLeakThreshold))

    for platform in releaseConfig['xulrunnerPlatforms']:
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
                repoPath=releaseConfig['sourceRepoPath'],
                buildToolsRepoPath=branchConfig['build_tools_repo_path'],
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                profiledBuild=None,
                mozconfig = '%s/%s/xulrunner' % (platform, releaseConfig['sourceRepoName']),
                buildRevision='%s_RELEASE' % releaseConfig['baseTag'],
                stageServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username_xulrunner'],
                stageGroup=branchConfig['stage_group'],
                stageSshKey=branchConfig['stage_ssh_xulrunner_key'],
                stageBasePath=branchConfig['stage_base_path_xulrunner'],
                codesighs=False,
                uploadPackages=True,
                uploadSymbols=True,
                createSnippet=False,
                doCleanup=True, # this will clean-up the mac build dirs, but not delete
                                # the entire thing
                buildSpace=pf.get('build_space', branchConfig['default_build_space']),
                productName='xulrunner',
                version=releaseConfig['milestone'],
                buildNumber=releaseConfig['buildNumber'],
                clobberURL=branchConfig['base_clobber_url'],
                packageSDK=True,
            )
            builders.append({
                'name': builderPrefix('xulrunner_%s_build' % platform),
                'slavenames': pf['slaves'],
                'category': 'release',
                'builddir': builderPrefix('xulrunner_%s_build' % platform),
                'factory': xulrunner_build_factory,
                'env': builder_env,
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix('xulrunner_%s_build' % platform),
                slaves=branchConfig['platforms']['linux']['slaves'],
                category='release',
                ))

    if releaseConfig['doPartnerRepacks']:
         for platform in releaseConfig['l10nPlatforms']:
             partner_repack_factory = PartnerRepackFactory(
                 hgHost=branchConfig['hghost'],
                 repoPath=releaseConfig['sourceRepoPath'],
                 buildToolsRepoPath=branchConfig['build_tools_repo_path'],
                 productName=releaseConfig['productName'],
                 version=releaseConfig['version'],
                 buildNumber=releaseConfig['buildNumber'],
                 partnersRepoPath=releaseConfig['partnersRepoPath'],
                 platformList=[platform],
                 stagingServer=releaseConfig['stagingServer'],
                 stageUsername=branchConfig['stage_username'],
                 stageSshKey=branchConfig['stage_ssh_key'],
             )
  
             if 'macosx64' in branchConfig['platforms']:
                 slaves = branchConfig['platforms']['macosx64']['slaves']
             else:
                 slaves = branchConfig['platforms']['macosx']['slaves']
             builders.append({
                 'name': builderPrefix('partner_repack', platform),
                 'slavenames': slaves,
                 'category': 'release',
                 'builddir': builderPrefix('partner_repack', platform),
                 'factory': partner_repack_factory,
                 'nextSlave': _nextFastReservedSlave,
                 'env': builder_env
             })

    for platform in releaseConfig['l10nPlatforms']:
        l10n_verification_factory = L10nVerifyFactory(
            hgHost=branchConfig['hghost'],
            buildToolsRepoPath=branchConfig['build_tools_repo_path'],
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
            'category': 'release',
            'builddir': builderPrefix('l10n_verification', platform),
            'factory': l10n_verification_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': builder_env,
        })


    updates_factory = ReleaseUpdatesFactory(
        hgHost=branchConfig['hghost'],
        repoPath=releaseConfig['sourceRepoPath'],
        buildToolsRepoPath=branchConfig['build_tools_repo_path'],
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
        commitPatcherConfig=(not staging),
        clobberURL=branchConfig['base_clobber_url'],
        oldRepoPath=releaseConfig['sourceRepoPath'],
        releaseNotesUrl=releaseConfig['releaseNotesUrl'],
        binaryName=releaseConfig['binaryName'],
        oldBinaryName=releaseConfig['oldBinaryName'],
        testOlderPartials=releaseConfig['testOlderPartials'],
    )

    builders.append({
        'name': builderPrefix('updates'),
        'slavenames': branchConfig['platforms']['linux']['slaves'],
        'category': 'release',
        'builddir': builderPrefix('updates'),
        'factory': updates_factory,
        'nextSlave': _nextFastReservedSlave,
        'env': builder_env,
    })


    for platform in sorted(releaseConfig['verifyConfigs'].keys()):
        update_verify_factory = UpdateVerifyFactory(
            hgHost=branchConfig['hghost'],
            buildToolsRepoPath=branchConfig['build_tools_repo_path'],
            verifyConfig=releaseConfig['verifyConfigs'][platform],
            clobberURL=branchConfig['base_clobber_url'],
        )

        builders.append({
            'name': builderPrefix('%s_update_verify' % platform),
            'slavenames': branchConfig['platforms'][platform]['slaves'],
            'category': 'release',
            'builddir': builderPrefix('%s_update_verify' % platform),
            'factory': update_verify_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': builder_env,
        })


    final_verification_factory = ReleaseFinalVerification(
        hgHost=branchConfig['hghost'],
        buildToolsRepoPath=branchConfig['build_tools_repo_path'],
        verifyConfigs=releaseConfig['verifyConfigs'],
        clobberURL=branchConfig['base_clobber_url'],
    )

    builders.append({
        'name': builderPrefix('final_verification'),
        'slavenames': branchConfig['platforms']['linux']['slaves'],
        'category': 'release',
        'builddir': builderPrefix('final_verification'),
        'factory': final_verification_factory,
        'nextSlave': _nextFastReservedSlave,
        'env': builder_env,
    })

    if releaseConfig['majorUpdateRepoPath']:
        # Not attached to any Scheduler
        major_update_factory = MajorUpdateFactory(
            hgHost=branchConfig['hghost'],
            repoPath=releaseConfig['majorUpdateRepoPath'],
            buildToolsRepoPath=branchConfig['build_tools_repo_path'],
            cvsroot=releaseConfig['cvsroot'],
            patcherToolsTag=releaseConfig['patcherToolsTag'],
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
            commitPatcherConfig=(not staging),
            clobberURL=branchConfig['base_clobber_url'],
            oldRepoPath=releaseConfig['sourceRepoPath'],
            triggerSchedulers=[builderPrefix('major_update_verify')],
            releaseNotesUrl=releaseConfig['majorUpdateReleaseNotesUrl'],
        )

        builders.append({
            'name': builderPrefix('major_update'),
            'slavenames': branchConfig['platforms']['linux']['slaves'],
            'category': 'release',
            'builddir': builderPrefix('major_update'),
            'factory': major_update_factory,
            'nextSlave': _nextFastReservedSlave,
            'env': builder_env,
        })

        for platform in sorted(releaseConfig['majorUpdateVerifyConfigs'].keys()):
            major_update_verify_factory = UpdateVerifyFactory(
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=branchConfig['build_tools_repo_path'],
                verifyConfig=releaseConfig['majorUpdateVerifyConfigs'][platform],
                clobberURL=branchConfig['base_clobber_url'],
            )

            builders.append({
                'name': builderPrefix('%s_major_update_verify' % platform),
                'slavenames': branchConfig['platforms'][platform]['slaves'],
                'category': 'release',
                'builddir': builderPrefix('%s_major_update_verify' % platform),
                'factory': major_update_verify_factory,
                'nextSlave': _nextFastReservedSlave,
                'env': builder_env,
            })

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
        oldVersion=releaseConfig['oldVersion'],
        hgHost=branchConfig['hghost'],
        repoPath=releaseConfig['sourceRepoPath'],
        buildToolsRepoPath=branchConfig['build_tools_repo_path'],
        credentialsFile=os.path.join(os.getcwd(), "BuildSlaves.py")
    )

    builders.append({
        'name': builderPrefix('bouncer_submitter'),
        'slavenames': branchConfig['platforms']['linux']['slaves'],
        'category': 'release',
        'builddir': builderPrefix('bouncer_submitter'),
        'factory': bouncer_submitter_factory,
        'env': builder_env,
    })

    #send a message when we receive the sendchange and start tagging
    status.append(ChangeNotifier(
            fromaddr="release@mozilla.org",
            relayhost="mail.build.mozilla.org",
            sendToInterestedUsers=False,
            extraRecipients=releaseConfig['AllRecipients'],
            branches=[releaseConfig['sourceRepoPath']],
            messageFormatter=createReleaseChangeMessage,
        ))
    #send a message when signing is complete
    status.append(ChangeNotifier(
            fromaddr="release@mozilla.org",
            relayhost="mail.build.mozilla.org",
            sendToInterestedUsers=False,
            extraRecipients=releaseConfig['AllRecipients'],
            branches=[builderPrefix('post_signing')],
            messageFormatter=createReleaseChangeMessage,
        ))

    #send the nice(passing) release messages to release@m.o (for now)
    status.append(MailNotifier(
            fromaddr='release@mozilla.org',
            sendToInterestedUsers=False,
            extraRecipients=releaseConfig['PassRecipients'],
            mode='passing',
            builders=notify_builders,
            relayhost='mail.build.mozilla.org',
            messageFormatter=createReleaseMessage,
        ))

    #send all release messages to release@m.o (for now)
    status.append(MailNotifier(
            fromaddr='release@mozilla.org',
            sendToInterestedUsers=False,
            extraRecipients=releaseConfig['AllRecipients'],
            mode='all',
            categories=['release'],
            relayhost='mail.build.mozilla.org',
            messageFormatter=createReleaseMessage,
        ))

    status.append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=branchConfig["tinderbox_tree"] + "-Release",
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org",],
        relayhost="mail.build.mozilla.org",
        builders=[b['name'] for b in builders],
        logCompression="bzip2")
    )

    status.append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=branchConfig["tinderbox_tree"] + "-Release",
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org",],
        relayhost="mail.build.mozilla.org",
        builders=[b['name'] for b in test_builders],
        logCompression="bzip2",
        errorparser="unittest")
    )

    builders.extend(test_builders)

    return {
            "builders": builders,
            "status": status,
            "change_source": change_source,
            "schedulers": schedulers,
            }
