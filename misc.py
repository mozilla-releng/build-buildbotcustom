from urlparse import urljoin

from buildbot.scheduler import Nightly, Scheduler
from buildbot.status.tinderbox import TinderboxMailNotifier
from buildbot.steps.shell import WithProperties

import buildbotcustom.changes.hgpoller
import buildbotcustom.process.factory
import buildbotcustom.log
import buildbotcustom.l10n
import buildbotcustom.scheduler
reload(buildbotcustom.changes.hgpoller)
reload(buildbotcustom.process.factory)
reload(buildbotcustom.log)
reload(buildbotcustom.l10n)
reload(buildbotcustom.scheduler)

from buildbotcustom.changes.hgpoller import HgPoller, HgAllLocalesPoller
from buildbotcustom.process.factory import NightlyBuildFactory, \
  NightlyRepackFactory, UnittestBuildFactory, CodeCoverageFactory, \
  UnittestPackagedBuildFactory
from buildbotcustom.scheduler import MozScheduler, NoMergeScheduler, \
  NightlyRebuild
from buildbotcustom.l10n import NightlyL10n

# This file contains misc. helper function that don't make sense to put in
# other files. For example, functions that are called in a master.cfg

def get_l10n_repositories(file, l10nRepoPath, relbranch):
    """Reads in a list of locale names and revisions for their associated
       repository from 'file'.
    """
    if not l10nRepoPath.endswith('/'):
        l10nRepoPath = l10nRepoPath + '/'
    repositories = {}
    for localeLine in open(file).readlines():
        locale, revision = localeLine.rstrip().split()
        if revision == 'FIXME':
            raise Exception('Found FIXME in %s for locale "%s"' % \
                           (file, locale))
        locale = urljoin(l10nRepoPath, locale)
        repositories[locale] = {
            'revision': revision,
            'relbranchOverride': relbranch,
            'bumpFiles': []
        }

    return repositories


# This function is used as fileIsImportant parameter for Buildbots that do both
# dep/nightlies and release builds. Because they build the same "branch" this
# allows us to have the release builder ignore HgPoller triggered changse
# and the dep builders only obey HgPoller/Force Build triggered ones.

def isHgPollerTriggered(change, hgUrl):
    if change.comments.find(hgUrl) > -1:
        return True
    return False

def generateTestBuilderNames(name_prefix, suites_name, suites):
    test_builders = []
    if isinstance(suites, dict) and "totalChunks" in suites:
        totalChunks = suites['totalChunks']
        for i in range(totalChunks):
            test_builders.append('%s %s-%i/%i' % \
                    (name_prefix, suites_name, i+1, totalChunks))
    else:
        test_builders.append('%s %s' % (name_prefix, suites_name))

    return test_builders

def generateTestBuilder(config, branch_name, platform, name_prefix, build_dir_prefix,
        suites_name, suites, mochitestLeakThreshold, crashtestLeakThreshold):
    builders = []
    pf = config['platforms'].get(platform, {})
    if isinstance(suites, dict) and "totalChunks" in suites:
        totalChunks = suites['totalChunks']
        for i in range(totalChunks):
            factory = UnittestPackagedBuildFactory(
                platform=platform,
                test_suites=[suites['suite']],
                mochitest_leak_threshold=mochitestLeakThreshold,
                crashtest_leak_threshold=crashtestLeakThreshold,
                hgHost=config['hghost'],
                repoPath=config['repo_path'],
                buildToolsRepoPath=config['build_tools_repo_path'],
                buildSpace=0.5,
                buildsBeforeReboot=config['platforms'][platform]['builds_before_reboot'],
                totalChunks=totalChunks,
                thisChunk=i+1,
                chunkByDir=suites.get('chunkByDir'),
                env=pf.get('unittest-env', {}),
            )
            builder = {
                'name': '%s %s-%i/%i' % (name_prefix, suites_name, i+1, totalChunks),
                'slavenames': config['platforms'][platform]['slaves'],
                'builddir': '%s-%s-%i' % (build_dir_prefix, suites_name, i+1),
                'factory': factory,
                'category': branch_name,
            }
            builders.append(builder)
    else:
        factory = UnittestPackagedBuildFactory(
            platform=platform,
            test_suites=suites,
            mochitest_leak_threshold=mochitestLeakThreshold,
            crashtest_leak_threshold=crashtestLeakThreshold,
            hgHost=config['hghost'],
            repoPath=config['repo_path'],
            buildToolsRepoPath=config['build_tools_repo_path'],
            buildSpace=0.5,
            buildsBeforeReboot=config['platforms'][platform]['builds_before_reboot'],
        )
        builder = {
            'name': '%s %s' % (name_prefix, suites_name),
            'slavenames': config['platforms'][platform]['slaves'],
            'builddir': '%s-%s' % (build_dir_prefix, suites_name),
            'factory': factory,
            'category': branch_name,
        }
        builders.append(builder)
    return builders



def generateBranchObjects(config, name):
    """name is the name of branch which is usually the last part of the path
       to the repository. For example, 'mozilla-central', 'tracemonkey', or
       'mozilla-1.9.1'.
       config is a dictionary containing all of the necessary configuration
       information for a branch. The required keys depends greatly on what's
       enabled for a branch (unittests, xulrunner, l10n, etc). The best way
       to figure out what you need to pass is by looking at existing configs
       and using 'buildbot checkconfig' to verify.
    """
    # We return this at the end
    branchObjects = {
        'builders': [],
        'change_source': [],
        'schedulers': [],
        'status': []
    }
    builders = []
    unittestBuilders = []
    triggeredUnittestBuilders = []
    nightlyBuilders = []
    xulrunnerNightlyBuilders = []
    debugBuilders = []
    weeklyBuilders = []
    # This dict provides a mapping between en-US nightly scheduler names
    # and l10n nightly scheduler names. It's filled out just below here.
    l10nNightlyBuilders = {}
    # generate a list of builders, nightly builders (names must be different)
    # for easy access
    for platform in config['platforms'].keys():
        pf = config['platforms'][platform]
        base_name = pf['base_name']
        if platform.endswith("-debug"):
            debugBuilders.append('%s build' % base_name)
            # Debug unittests
            if pf.get('enable_unittests'):
                test_builders = []
                base_name = config['platforms'][platform.replace("-debug", "")]['base_name']
                for suites_name, suites in config['unittest_suites']:
                    test_builders.extend(generateTestBuilderNames('%s debug test' % base_name, suites_name, suites))
                triggeredUnittestBuilders.append(('%s-%s-unittest' % (name, platform), test_builders, True))
            # Skip l10n, unit tests and nightlies for debug builds
            continue
        else:
            builders.append('%s build' % base_name)

        builder = '%s nightly' % base_name
        nightlyBuilders.append(builder)
        if config['enable_shark'] and platform in ('macosx'):
            nightlyBuilders.append('%s shark' % base_name)
        if config['enable_l10n'] and platform in ('linux','win32','macosx'):
            l10nNightlyBuilders[builder] = {}
            l10nNightlyBuilders[builder]['tree'] = config['l10n_tree']
            l10nNightlyBuilders[builder]['l10n_builder'] = \
                '%s %s %s l10n' % (config['product_name'].capitalize(),
                                   name, platform)
            l10nNightlyBuilders[builder]['platform'] = platform
        # Regular unittest builds
        if pf.get('enable_unittests'):
            unittestBuilders.append('%s unit test' % base_name)
            test_builders = []
            for suites_name, suites in config['unittest_suites']:
                test_builders.extend(generateTestBuilderNames('%s test' % base_name, suites_name, suites))
            triggeredUnittestBuilders.append(('%s-%s-unittest' % (name, platform), test_builders, False))
        # Optimized unittest builds
        if pf.get('enable_opt_unittests'):
            test_builders = []
            for suites_name, suites in config['unittest_suites']:
                test_builders.extend(generateTestBuilderNames('%s opt test' % base_name, suites_name, suites))
            triggeredUnittestBuilders.append(('%s-%s-opt-unittest' % (name, platform), test_builders, True))
        if config['enable_codecoverage'] and platform in ('linux',):
            weeklyBuilders.append('%s code coverage' % base_name)
        if config['enable_xulrunner'] and platform not in ('wince'):
            xulrunnerNightlyBuilders.append('%s xulrunner' % base_name)

    # Currently, each branch goes to a different tree
    # If this changes in the future this may have to be
    # moved out of the loop
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=builders + nightlyBuilders,
        logCompression="bzip2"
    ))
    # XULRunner builds
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['xulrunner_tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=xulrunnerNightlyBuilders,
        logCompression="bzip2"
    ))
    # Separate notifier for unittests, since they need to be run through
    # the unittest errorparser
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=unittestBuilders + debugBuilders,
        logCompression="bzip2",
        errorparser="unittest"
    ))
    # Weekly builds (currently only code coverage) go to a different tree
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['weekly_tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=weeklyBuilders,
        logCompression="bzip2",
        errorparser="unittest"
    ))

    if config['enable_l10n']:
        l10n_builders = []
        for b in l10nNightlyBuilders:
            l10n_builders.append(l10nNightlyBuilders[b]['l10n_builder'])
            l10n_builders.append(l10nNightlyBuilders[b]['l10n_builder'] + " build")
        l10n_binaryURL = config['enUS_binaryURL']
        if l10n_binaryURL.endswith('/'):
            l10n_binaryURL = l10n_binaryURL[:-1]
        l10n_binaryURL += "-l10n"

        # This notifies all l10n related build objects to Mozilla-l10n
        branchObjects['status'].append(TinderboxMailNotifier(
            fromaddr="bootstrap@mozilla.com",
            tree=config['l10n_tinderbox_tree'],
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
            relayhost="mail.build.mozilla.org",
            logCompression="bzip2",
            builders=l10n_builders,
            binaryURL=l10n_binaryURL
        ))

        # We only want the builds from the specified builders
        # since their builds have a build property called "locale"
        branchObjects['status'].append(TinderboxMailNotifier(
            fromaddr="bootstrap@mozilla.com",
            tree=WithProperties(config['l10n_tinderbox_tree'] + "-%(locale)s"),
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
            relayhost="mail.build.mozilla.org",
            logCompression="bzip2",
            builders=l10n_builders,
            binaryURL=l10n_binaryURL
        ))

    # change sources
    branchObjects['change_source'].append(HgPoller(
        hgURL=config['hgurl'],
        branch=config['repo_path'],
        pushlogUrlOverride='%s/%s/pushlog' % (config['hgurl'],
                                              config['repo_path']),
        pollInterval=1*60
    ))

    if config['enable_l10n']:
        hg_all_locales_poller = HgAllLocalesPoller(hgURL = config['hgurl'],
                            repositoryIndex = config['l10n_repo_path'],
                            pollInterval = 15*60)
        hg_all_locales_poller.parallelRequests = 1
        branchObjects['change_source'].append(hg_all_locales_poller)

    # schedulers
    # this one gets triggered by the HG Poller
    branchObjects['schedulers'].append(MozScheduler(
        name=name,
        branch=config['repo_path'],
        treeStableTimer=3*60,
        idleTimeout=config.get('idle_timeout', None),
        builderNames=builders + unittestBuilders + debugBuilders,
        fileIsImportant=lambda c: isHgPollerTriggered(c, config['hgurl'])
    ))

    for scheduler_branch, test_builders, merge in triggeredUnittestBuilders:
        scheduler_name = scheduler_branch
        if not merge:
            branchObjects['schedulers'].append(NoMergeScheduler(name=scheduler_name, branch=scheduler_branch, builderNames=test_builders, treeStableTimer=0))
        else:
            branchObjects['schedulers'].append(Scheduler(name=scheduler_name, branch=scheduler_branch, builderNames=test_builders, treeStableTimer=0))

        branchObjects['schedulers'].append(NightlyRebuild(name=scheduler_name+"-nightly",
            builderNames=test_builders,
            dayOfWeek=0, # Monday
            hour=3, minute=2, # at 3:02 am local time
            numberOfBuildsToTrigger=2,
            mergeBuilds=False))
        branchObjects['status'].append(TinderboxMailNotifier(
            fromaddr="mozilla2.buildbot@build.mozilla.org",
            tree=config['packaged_unittest_tinderbox_tree'],
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
            relayhost="mail.build.mozilla.org",
            builders=test_builders,
            logCompression="bzip2",
            errorparser="unittest"
        ))

    # Now, setup the nightly en-US schedulers and maybe,
    # their downstream l10n ones
    for builder in nightlyBuilders + xulrunnerNightlyBuilders:
        nightly_scheduler=Nightly(
            name=builder,
            branch=config['repo_path'],
            # bug 482123 - keep the minute to avoid problems with DST changes
            hour=config['start_hour'], minute=config['start_minute'],
            builderNames=[builder]
        )
        branchObjects['schedulers'].append(nightly_scheduler)

        if config['enable_l10n'] and builder in l10nNightlyBuilders:
            l10n_builder = l10nNightlyBuilders[builder]['l10n_builder']
            platform = l10nNightlyBuilders[builder]['platform']
            tree = l10nNightlyBuilders[builder]['tree']
            branchObjects['schedulers'].append(NightlyL10n(
                                   name=l10n_builder,
                                   platform=platform,
                                   tree=tree,
                                   builderNames=[l10n_builder],
                                   repoType='hg',
                                   branch=config['repo_path'],
                                   baseTag='default',
                                   localesFile=config['allLocalesFile']
                                  ))

    for builder in weeklyBuilders:
        weekly_scheduler=Nightly(
            name=builder,
            branch=config['repo_path'],
            dayOfWeek=5, # Saturday
            hour=[3], minute=[02],
            builderNames=[builder],
        )
        branchObjects['schedulers'].append(weekly_scheduler)

    for platform in sorted(config['platforms'].keys()):
        # shorthand
        pf = config['platforms'][platform]

        leakTest = False
        codesighs = True
        uploadPackages = True
        uploadSymbols = False
        packageTests = False
        talosMasters = config['talos_masters']
        unittestBranch = "%s-%s-opt-unittest" % (name, platform)
        tinderboxBuildsDir = None
        if platform.find('-debug') > -1:
            leakTest = True
            codesighs = False
            if not pf.get('enable_unittests'):
                uploadPackages = False
            else:
                packageTests = True
            talosMasters = None
            # Platform already has the -debug suffix
            unittestBranch = "%s-%s-unittest" % (name, platform)
            tinderboxBuildsDir = "%s-%s" % (name, platform)
        elif pf.get('enable_opt_unittests'):
            packageTests = True

        # Allow for test packages on platforms that can't be tested
        # on the same master.
        packageTests = pf.get('packageTests', packageTests)

        if platform.find('win') > -1 or platform.find('64') > -1:
            codesighs = False
        if 'upload_symbols' in pf and pf['upload_symbols']:
            uploadSymbols = True

        buildSpace = pf.get('build_space', config['default_build_space'])
        clobberTime = pf.get('clobber_time', config['default_clobber_time'])
        mochitestLeakThreshold = pf.get('mochitest_leak_threshold', None)
        crashtestLeakThreshold = pf.get('crashtest_leak_threshold', None)
        checkTest = pf.get('enable_checktests', False)
        valgrindCheck = pf.get('enable_valgrind_checktests', False)

        mozilla2_dep_factory = NightlyBuildFactory(
            env=pf['env'],
            objdir=pf['platform_objdir'],
            platform=platform,
            hgHost=config['hghost'],
            repoPath=config['repo_path'],
            buildToolsRepoPath=config['build_tools_repo_path'],
            configRepoPath=config['config_repo_path'],
            configSubDir=config['config_subdir'],
            profiledBuild=pf['profiled_build'],
            productName=config['product_name'],
            mozconfig=pf['mozconfig'],
            stageServer=config['stage_server'],
            stageUsername=config['stage_username'],
            stageGroup=config['stage_group'],
            stageSshKey=config['stage_ssh_key'],
            stageBasePath=config['stage_base_path'],
            graphServer=config['graph_server'],
            graphSelector=config['graph_selector'],
            graphBranch=config['tinderbox_tree'],
            baseName=pf['base_name'],
            leakTest=leakTest,
            checkTest=checkTest,
            valgrindCheck=valgrindCheck,
            codesighs=codesighs,
            uploadPackages=uploadPackages,
            uploadSymbols=False,
            buildSpace=buildSpace,
            clobberURL=config['base_clobber_url'],
            clobberTime=clobberTime,
            buildsBeforeReboot=pf['builds_before_reboot'],
            talosMasters=talosMasters,
            packageTests=packageTests,
            unittestMasters=config['unittest_masters'],
            unittestBranch=unittestBranch,
            tinderboxBuildsDir=tinderboxBuildsDir,
        )
        mozilla2_dep_builder = {
            'name': '%s build' % pf['base_name'],
            'slavenames': pf['slaves'],
            'builddir': '%s-%s' % (name, platform),
            'factory': mozilla2_dep_factory,
            'category': name,
        }
        branchObjects['builders'].append(mozilla2_dep_builder)

        # skip nightlies for debug builds
        if platform.find('debug') > -1:
            if pf.get('enable_unittests'):
                for suites_name, suites in config['unittest_suites']:
                    if "macosx" in platform and 'mochitest-a11y' in suites:
                        suites = suites[:]
                        suites.remove('mochitest-a11y')

                    base_name = config['platforms'][platform.replace("-debug", "")]['base_name']

                    branchObjects['builders'].extend(generateTestBuilder(
                        config, name, platform, "%s debug test" % base_name,
                        "%s-%s-unittest" % (name, platform),
                        suites_name, suites, mochitestLeakThreshold,
                        crashtestLeakThreshold))
            continue

        nightly_builder = '%s nightly' % pf['base_name']

        triggeredSchedulers=None
        if platform in ('linux', 'macosx', 'win32') and \
           nightly_builder in l10nNightlyBuilders:
            triggeredSchedulers=[l10nNightlyBuilders[nightly_builder]['l10n_builder']]

        mozilla2_nightly_factory = NightlyBuildFactory(
            env=pf['env'],
            objdir=pf['platform_objdir'],
            platform=platform,
            hgHost=config['hghost'],
            repoPath=config['repo_path'],
            buildToolsRepoPath=config['build_tools_repo_path'],
            configRepoPath=config['config_repo_path'],
            configSubDir=config['config_subdir'],
            profiledBuild=pf['profiled_build'],
            productName=config['product_name'],
            mozconfig=pf['mozconfig'],
            stageServer=config['stage_server'],
            stageUsername=config['stage_username'],
            stageGroup=config['stage_group'],
            stageSshKey=config['stage_ssh_key'],
            stageBasePath=config['stage_base_path'],
            codesighs=False,
            uploadPackages=uploadPackages,
            uploadSymbols=uploadSymbols,
            nightly=True,
            createSnippet=config['create_snippet'],
            ausBaseUploadDir=config['aus2_base_upload_dir'],
            updatePlatform=pf['update_platform'],
            downloadBaseURL=config['download_base_url'],
            ausUser=config['aus2_user'],
            ausHost=config['aus2_host'],
            buildSpace=buildSpace,
            clobberURL=config['base_clobber_url'],
            clobberTime=clobberTime,
            buildsBeforeReboot=pf['builds_before_reboot'],
            talosMasters=talosMasters,
            packageTests=packageTests,
            unittestMasters=config['unittest_masters'],
            unittestBranch=unittestBranch,
            triggerBuilds=config['enable_l10n'],
            triggeredSchedulers=triggeredSchedulers,
            tinderboxBuildsDir=tinderboxBuildsDir,
        )

        mozilla2_nightly_builder = {
            'name': nightly_builder,
            'slavenames': pf['slaves'],
            'builddir': '%s-%s-nightly' % (name, platform),
            'factory': mozilla2_nightly_factory,
            'category': name,
        }
        branchObjects['builders'].append(mozilla2_nightly_builder)

        if config['enable_l10n']:
            if platform in ('linux','win32','macosx'):
                mozilla2_l10n_nightly_factory = NightlyRepackFactory(
                    hgHost=config['hghost'],
                    tree=config['l10n_tree'],
                    project=config['product_name'],
                    appName=config['app_name'],
                    enUSBinaryURL=config['enUS_binaryURL'],
                    nightly=True,
                    l10nNightlyUpdate=config['l10nNightlyUpdate'],
                    l10nDatedDirs=config['l10nDatedDirs'],
                    ausBaseUploadDir=config['aus2_base_upload_dir'],
                    updatePlatform=pf['update_platform'],
                    downloadBaseURL=config['download_base_url'],
                    ausUser=config['aus2_user'],
                    ausHost=config['aus2_host'],
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageSshKey=config['stage_ssh_key'],
                    repoPath=config['repo_path'],
                    l10nRepoPath=config['l10n_repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    compareLocalesRepoPath=config['compare_locales_repo_path'],
                    compareLocalesTag=config['compare_locales_tag'],
                    buildSpace=2,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                )
                mozilla2_l10n_nightly_builder = {
                    'name': l10nNightlyBuilders[nightly_builder]['l10n_builder'],
                    'slavenames': config['l10n_slaves'][platform],
                    'builddir': '%s-%s-l10n-nightly' % (name, platform),
                    'factory': mozilla2_l10n_nightly_factory,
                    'category': name,
                }
                branchObjects['builders'].append(mozilla2_l10n_nightly_builder)
                mozilla2_l10n_dep_factory = NightlyRepackFactory(
                    hgHost=config['hghost'],
                    tree=config['l10n_tree'],
                    project=config['product_name'],
                    appName=config['app_name'],
                    enUSBinaryURL=config['enUS_binaryURL'],
                    nightly=False,
                    l10nDatedDirs=config['l10nDatedDirs'],
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageSshKey=config['stage_ssh_key'],
                    repoPath=config['repo_path'],
                    l10nRepoPath=config['l10n_repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    compareLocalesRepoPath=config['compare_locales_repo_path'],
                    compareLocalesTag=config['compare_locales_tag'],
                    buildSpace=2,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                )
                mozilla2_l10n_dep_builder = {
                    'name': l10nNightlyBuilders[nightly_builder]['l10n_builder'] + " build",
                    'slavenames': config['l10n_slaves'][platform],
                    'builddir': '%s-%s-l10n-dep' % (name, platform),
                    'factory': mozilla2_l10n_dep_factory,
                    'category': name,
                }
                branchObjects['builders'].append(mozilla2_l10n_dep_builder)

        if pf.get('enable_unittests'):
            runA11y = True
            if platform == 'macosx':
                runA11y = config['enable_mac_a11y']

            unittest_factory = UnittestBuildFactory(
                env=pf.get('unittest-env', {}),
                platform=platform,
                productName=config['product_name'],
                config_repo_path=config['config_repo_path'],
                config_dir=config['config_subdir'],
                objdir=config['objdir_unittests'],
                hgHost=config['hghost'],
                repoPath=config['repo_path'],
                buildToolsRepoPath=config['build_tools_repo_path'],
                buildSpace=config['unittest_build_space'],
                clobberURL=config['base_clobber_url'],
                clobberTime=clobberTime,
                buildsBeforeReboot=pf['builds_before_reboot'],
                run_a11y=runA11y,
                mochitest_leak_threshold=mochitestLeakThreshold,
                crashtest_leak_threshold=crashtestLeakThreshold,
                stageServer=config['stage_server'],
                stageUsername=config['stage_username'],
                stageSshKey=config['stage_ssh_key'],
                unittestMasters=config['unittest_masters'],
                unittestBranch="%s-%s-unittest" % (name, platform),
                uploadPackages=True,
            )
            unittest_builder = {
                'name': '%s unit test' % pf['base_name'],
                'slavenames': pf['slaves'],
                'builddir': '%s-%s-unittest' % (name, platform),
                'factory': unittest_factory,
                'category': name,
            }
            branchObjects['builders'].append(unittest_builder)

        for suites_name, suites in config['unittest_suites']:
            runA11y = True
            if platform == 'macosx':
                runA11y = config['enable_mac_a11y']

            # For the regular unittest build, run the a11y suite if
            # enable_mac_a11y is set on mac
            if not runA11y and 'mochitest-a11y' in suites:
                suites = suites[:]
                suites.remove('mochitest-a11y')

            if pf.get('enable_unittests'):
                branchObjects['builders'].extend(generateTestBuilder(
                    config, name, platform, "%s test" % pf['base_name'],
                    "%s-%s-unittest" % (name, platform),
                    suites_name, suites, mochitestLeakThreshold,
                    crashtestLeakThreshold))

            # Remove mochitest-a11y from other types of builds, since they're not
            # built with a11y enabled
            if platform.startswith("macosx") and 'mochitest-a11y' in suites:
                # Create a new factory that doesn't have mochitest-a11y
                suites = suites[:]
                suites.remove('mochitest-a11y')

            if pf.get('enable_opt_unittests'):
                branchObjects['builders'].extend(generateTestBuilder(
                    config, name, platform, "%s opt test" % pf['base_name'],
                    "%s-%s-opt-unittest" % (name, platform),
                    suites_name, suites, mochitestLeakThreshold,
                    crashtestLeakThreshold))

        if config['enable_codecoverage']:
            # We only do code coverage builds on linux right now
            if platform == 'linux':
                codecoverage_factory = CodeCoverageFactory(
                    platform=platform,
                    productName=config['product_name'],
                    config_repo_path=config['config_repo_path'],
                    config_dir=config['config_subdir'],
                    objdir=config['objdir_unittests'],
                    hgHost=config['hghost'],
                    repoPath=config['repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    buildSpace=5,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                    buildsBeforeReboot=pf['builds_before_reboot'],
                    mochitest_leak_threshold=mochitestLeakThreshold,
                    crashtest_leak_threshold=crashtestLeakThreshold,
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageSshKey=config['stage_ssh_key'],
                )
                codecoverage_builder = {
                    'name': '%s code coverage' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s-codecoverage' % (name, platform),
                    'factory': codecoverage_factory,
                    'category': name,
                }
                branchObjects['builders'].append(codecoverage_builder)

        if config['enable_shark'] and platform in ('macosx'):
             mozilla2_shark_factory = NightlyBuildFactory(
                 env= pf['env'],
                 objdir=config['objdir'],
                 platform=platform,
                 hgHost=config['hghost'],
                 repoPath=config['repo_path'],
                 buildToolsRepoPath=config['build_tools_repo_path'],
                 configRepoPath=config['config_repo_path'],
                 configSubDir=config['config_subdir'],
                 profiledBuild=False,
                 productName=config['product_name'],
                 mozconfig='macosx/%s/shark' % name,
                 stageServer=config['stage_server'],
                 stageUsername=config['stage_username'],
                 stageGroup=config['stage_group'],
                 stageSshKey=config['stage_ssh_key'],
                 stageBasePath=config['stage_base_path'],
                 codesighs=False,
                 uploadPackages=uploadPackages,
                 uploadSymbols=False,
                 nightly=True,
                 createSnippet=False,
                 buildSpace=buildSpace,
                 clobberURL=config['base_clobber_url'],
                 clobberTime=clobberTime,
                 buildsBeforeReboot=pf['builds_before_reboot']
             )
             mozilla2_shark_builder = {
                 'name': '%s shark' % pf['base_name'],
                 'slavenames': pf['slaves'],
                 'builddir': '%s-%s-shark' % (name, platform),
                 'factory': mozilla2_shark_factory,
                 'category': name,
             }
             branchObjects['builders'].append(mozilla2_shark_builder)

        if config['enable_xulrunner'] and platform not in ('wince'):
             xr_env = pf['env'].copy()
             xr_env['SYMBOL_SERVER_USER'] = config['stage_username_xulrunner']
             xr_env['SYMBOL_SERVER_PATH'] = config['symbol_server_xulrunner_path']
             xr_env['SYMBOL_SERVER_SSH_KEY'] = \
                 xr_env['SYMBOL_SERVER_SSH_KEY'].replace(config['stage_ssh_key'], config['stage_ssh_xulrunner_key'])
             mozilla2_xulrunner_factory = NightlyBuildFactory(
                 env=xr_env,
                 objdir=pf['platform_objdir'],
                 platform=platform,
                 hgHost=config['hghost'],
                 repoPath=config['repo_path'],
                 buildToolsRepoPath=config['build_tools_repo_path'],
                 configRepoPath=config['config_repo_path'],
                 configSubDir=config['config_subdir'],
                 profiledBuild=False,
                 productName='xulrunner',
                 mozconfig='%s/%s/xulrunner' % (platform, name),
                 stageServer=config['stage_server'],
                 stageUsername=config['stage_username_xulrunner'],
                 stageGroup=config['stage_group'],
                 stageSshKey=config['stage_ssh_xulrunner_key'],
                 stageBasePath=config['stage_base_path_xulrunner'],
                 codesighs=False,
                 uploadPackages=uploadPackages,
                 uploadSymbols=True,
                 nightly=True,
                 createSnippet=False,
                 buildSpace=buildSpace,
                 clobberURL=config['base_clobber_url'],
                 clobberTime=clobberTime,
                 buildsBeforeReboot=pf['builds_before_reboot'],
                 packageSDK=True,
             )
             mozilla2_xulrunner_builder = {
                 'name': '%s xulrunner' % pf['base_name'],
                 'slavenames': pf['slaves'],
                 'builddir': '%s-%s-xulrunner' % (name, platform),
                 'factory': mozilla2_xulrunner_factory,
                 'category': name,
             }
             branchObjects['builders'].append(mozilla2_xulrunner_builder)

    return branchObjects
