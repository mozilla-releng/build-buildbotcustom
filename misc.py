try:
    import json
    assert json  # pyflakes
except:
    import simplejson as json
import collections
import random
import re
import sys
import os
from copy import deepcopy
import inspect
from functools import wraps

from twisted.python import log

from buildbot.scheduler import Nightly, Scheduler, Triggerable
from buildbot.schedulers.filter import ChangeFilter
from buildbot.steps.shell import WithProperties
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, EXCEPTION, RETRY
from buildbot.util import now

import buildbotcustom.common
import buildbotcustom.changes.hgpoller
import buildbotcustom.process.factory
import buildbotcustom.l10n
import buildbotcustom.scheduler
import buildbotcustom.status.mail
import buildbotcustom.status.generators
import buildbotcustom.status.queued_command
import buildbotcustom.misc_scheduler
import build.paths
import mozilla_buildtools.queuedir
reload(buildbotcustom.common)
reload(buildbotcustom.changes.hgpoller)
reload(buildbotcustom.process.factory)
reload(buildbotcustom.l10n)
reload(buildbotcustom.scheduler)
reload(buildbotcustom.status.mail)
reload(buildbotcustom.status.generators)
reload(buildbotcustom.misc_scheduler)
reload(build.paths)
reload(mozilla_buildtools.queuedir)

from buildbotcustom.common import normalizeName
from buildbotcustom.changes.hgpoller import HgPoller, HgAllLocalesPoller
from buildbotcustom.process.factory import NightlyBuildFactory, \
    NightlyRepackFactory, \
    TryBuildFactory, ScriptFactory, SigningScriptFactory, rc_eval_func
from buildbotcustom.scheduler import BuilderChooserScheduler, \
    PersistentScheduler, makePropertiesScheduler, SpecificNightly, EveryNthScheduler
from buildbotcustom.l10n import TriggerableL10n
from buildbotcustom.status.mail import MercurialEmailLookup, ChangeNotifier
from buildbotcustom.status.generators import buildTryChangeMessage
from buildbotcustom.env import MozillaEnvironments
from buildbotcustom.misc_scheduler import tryChooser, buildIDSchedFunc, \
    buildUIDSchedFunc, lastGoodFunc, lastRevFunc

# This file contains misc. helper function that don't make sense to put in
# other files. For example, functions that are called in a master.cfg

# This function is used as fileIsImportant parameter for Buildbots that do both
# dep/nightlies and release builds. Because they build the same "branch" this
# allows us to have the release builder ignore HgPoller triggered changse
# and the dep builders only obey HgPoller/Force Build triggered ones.


def isHgPollerTriggered(change, hgUrl):
    if (change.revlink and hgUrl in change.revlink) or \
            change.comments.find(hgUrl) > -1:
        return True


def shouldBuild(change):
    """check for commit message disabling build for this change"""
    return "DONTBUILD" not in change.comments


_product_excludes = {
    '__all': [
        re.compile('^CLOBBER'),
        re.compile('/docs/'),
        re.compile('^tools/mercurial/hgsetup/'),
    ],
    'firefox': [
        re.compile('android'),

        re.compile('gonk'),

        re.compile('^b2g/'),
        re.compile('^build/mobile'),
        re.compile('^mobile/'),
        re.compile('^testing/mochitest/runrobocop.py')
    ],
    'mobile': [
        re.compile('gonk'),

        re.compile('^b2g/'),

        re.compile('^accessible/public/ia2'),
        re.compile('^accessible/public/msaa'),
        re.compile('^accessible/src/mac'),
        re.compile('^accessible/src/windows'),

        re.compile('^browser/'),

        re.compile('^build/macosx'),
        re.compile('^build/package/mac_osx'),
        re.compile('^build/win32'),
        re.compile('^build/win64'),

        re.compile('^devtools/client'),

        re.compile('^toolkit/system/osxproxy'),
        re.compile('^toolkit/system/windowsproxy'),
        re.compile('^toolkit/themes/osx'),

        re.compile('^webapprt/win'),
        re.compile('^webapprt/mac'),

        re.compile('^widget/cocoa'),
        re.compile('^widget/windows'),

        re.compile('^xulrunner/')
    ],
    'b2g': [
        re.compile('^accessible/public/ia2'),
        re.compile('^accessible/public/msaa'),
        re.compile('^accessible/src/mac'),
        re.compile('^accessible/src/windows'),

        re.compile('^browser/'),

        re.compile('^build/macosx'),
        re.compile('^build/mobile'),
        re.compile('^build/package/mac_osx'),
        re.compile('^build/win32'),
        re.compile('^build/win64'),

        re.compile('^devtools/client'),

        re.compile('^mobile/'),

        re.compile('^webapprt/win'),
        re.compile('^webapprt/mac'),

        re.compile('^widget/cocoa'),
        re.compile('^widget/gonk'),
        re.compile('^widget/windows'),

        re.compile('^xulrunner/')
    ],
    'thunderbird': [
        re.compile("^im/"),
        re.compile("^suite/")
    ],
}


def isImportantForProduct(change, product):
    """Handles product specific handling of important files"""
    # For each file, check each product's exclude list
    # If a file is not excluded, then the change is important
    # If all files are excluded, then the change is not important
    # As long as hgpoller's 'overflow' marker isn't excluded, it will cause all
    # products to build
    excludes = _product_excludes.get(product, []) + _product_excludes.get('__all', [])
    for f in change.files:
        excluded = any(e.search(f) for e in excludes)
        if not excluded:
            log.msg("%s important for %s because of %s" % (
                change.revision, product, f))
            return True

    # Everything was excluded
    log.msg("%s not important for %s because all files were excluded" %
            (change.revision, product))
    return False


def makeImportantFunc(hgurl, product):
    def isImportant(c):
        if not isHgPollerTriggered(c, hgurl):
            return False
        if not shouldBuild(c):
            return False
        # No product is specified, so all changes are important
        if product is None:
            return True
        return isImportantForProduct(c, product)
    return isImportant


def isImportantL10nFile(change, l10nModules):
    for f in change.files:
        for basepath in l10nModules:
            if f.startswith(basepath):
                return True
    return False


def changeContainsProduct(change, productName):
    products = change.properties.getProperty("products")
    if isinstance(products, basestring) and \
            productName in products.split(','):
            return True
    return False


def changeContainsScriptRepoRevision(change, revision):
    script_repo_revision = change.properties.getProperty("script_repo_revision")
    if script_repo_revision == revision:
        log.msg("%s revision matches script_repo_revision %s" % (revision, script_repo_revision))
        return True
    log.msg("%s revision does not match script_repo_revision %s" % (revision, script_repo_revision))
    return False


def changeContainsProperties(change, props={}):
    for prop, value in props.iteritems():
        if change.properties.getProperty(prop) != value:
            return False
    return True


def generateTestBuilderNames(name_prefix, suites_name, suites):
    test_builders = []
    if isinstance(suites, dict) and "totalChunks" in suites:
        totalChunks = suites['totalChunks']
        for i in range(totalChunks):
            test_builders.append('%s %s-%i' %
                                 (name_prefix, suites_name, i + 1))
    else:
        test_builders.append('%s %s' % (name_prefix, suites_name))

    return test_builders


def _getLastTimeOnBuilder(builder, slavename):
    # New builds are at the end of the buildCache, so
    # examine it backwards
    buildNumbers = reversed(sorted(builder.builder_status.buildCache.keys()))
    for buildNumber in buildNumbers:
        try:
            build = builder.builder_status.buildCache[buildNumber]
            # Skip non-successful builds
            if build.getResults() != 0:
                continue
            if build.slavename == slavename:
                return build.finished
        except KeyError:
            continue
    return None


def _recentSort(builder):
    def sortfunc(s1, s2):
        t1 = _getLastTimeOnBuilder(builder, s1.slave.slavename)
        t2 = _getLastTimeOnBuilder(builder, s2.slave.slavename)
        return cmp(t1, t2)
    return sortfunc


def safeNextSlave(func):
    """Wrapper around nextSlave functions that catch exceptions , log them, and
    choose a random slave instead"""
    @wraps(func)
    def _nextSlave(builder, available_slaves):
        try:
            return func(builder, available_slaves)
        except Exception:
            log.msg("Error choosing next slave for builder '%s', choosing"
                    " randomly instead" % builder.name)
            log.err()
            if available_slaves:
                return random.choice(available_slaves)
            return None
    return _nextSlave


def _get_pending(builder):
    """Returns the pending build requests for this builder"""
    frame = inspect.currentframe()
    # Walk up the stack until we find 't', a db transaction object. It allows
    # us to make synchronous calls to the db from this thread.
    # We need to commit this horrible crime because
    # a) we're running in a thread
    # b) so we can't use the db's existing sync query methods since they use a
    # db connection created in another thread
    # c) nor can we use deferreds (threads and deferreds don't play well
    # together)
    # d) there's no other way to get a db connection
    while 't' not in frame.f_locals:
        frame = frame.f_back
    t = frame.f_locals['t']
    del frame

    return builder._getBuildable(t, None)


def is_spot(name):
    return "-spot-" in name


def _classifyAWSSlaves(slaves):
    """
    Partitions slaves into three groups: inhouse, ondemand, spot according to
    their name. Returns three lists:
        inhouse, ondemand, spot
    """
    inhouse = []
    ondemand = []
    spot = []
    for s in slaves:
        if not s.slave:
            continue
        name = s.slave.slavename
        if is_spot(name):
            spot.append(s)
        elif 'ec2' in name:
            ondemand.append(s)
        else:
            inhouse.append(s)

    return inhouse, ondemand, spot


def _nextAWSSlave(aws_wait=None, recentSort=False):
    """
    Returns a nextSlave function that pick the next available slave, with some
    special consideration for AWS instances:
        - If the request is very new, wait for an inhouse instance to pick it
          up. Set aws_wait to the number of seconds to wait before using an AWS
          instance. Set to None to disable this behaviour.

        - Otherwise give the job to a spot instance

    If recentSort is True then pick slaves that most recently did this type of
    build. Otherwise pick randomly.

    """
    log.msg("nextAWSSlave: start")

    if recentSort:
        def sorter(slaves, builder):
            if not slaves:
                return None
            return sorted(slaves, _recentSort(builder))[-1]
    else:
        def sorter(slaves, builder):
            if not slaves:
                return None
            return random.choice(slaves)

    def _nextSlave(builder, available_slaves):
        # Partition the slaves into 3 groups:
        # - inhouse slaves
        # - ondemand slaves
        # - spot slaves
        # We always prefer to run on inhouse. We'll wait up to aws_wait
        # seconds for one to show up!

        # Easy! If there are no available slaves, don't return any!
        if not available_slaves:
            return None

        inhouse, ondemand, spot = _classifyAWSSlaves(available_slaves)

        # Always prefer inhouse slaves
        if inhouse:
            log.msg("nextAWSSlave: Choosing inhouse because it's the best!")
            return sorter(inhouse, builder)

        # We need to look at our build requests if we need to know # of
        # retries, or if we're going to be waiting for an inhouse slave to come
        # online.
        if aws_wait or spot:
            requests = _get_pending(builder)
            if requests:
                oldestRequestTime = sorted(requests, key=lambda r:
                                           r.submittedAt)[0].submittedAt
            else:
                oldestRequestTime = 0

        if aws_wait and now() - oldestRequestTime < aws_wait:
            log.msg("nextAWSSlave: Waiting for inhouse slaves to show up")
            return None

        if spot:
            log.msg("nextAWSSlave: Choosing spot since there aren't any retries")
            return sorter(spot, builder)
        elif ondemand:
            log.msg("nextAWSSlave: Choosing ondemand since there aren't any spot available")
            return sorter(ondemand, builder)
        else:
            log.msg("nextAWSSlave: No slaves - returning None")
            return None
    return _nextSlave

_nextAWSSlave_sort = safeNextSlave(_nextAWSSlave(aws_wait=0, recentSort=True))
_nextAWSSlave_nowait = safeNextSlave(_nextAWSSlave())


@safeNextSlave
def _nextSlave(builder, available_slaves):
    # Choose the slave that was most recently on this builder
    if available_slaves:
        return sorted(available_slaves, _recentSort(builder))[-1]
    else:
        return None


def _nextIdleSlave(nReserved):
    """Return a nextSlave function that will only return a slave to run a build
    if there are at least nReserved slaves available."""
    @safeNextSlave
    def _nextslave(builder, available_slaves):
        if len(available_slaves) <= nReserved:
            return None
        return sorted(available_slaves, _recentSort(builder))[-1]
    return _nextslave

# Globals for mergeRequests
nomergeBuilders = set()
# Default to max of 3 merged requests.
builderMergeLimits = collections.defaultdict(lambda: 3)
# For tracking state in mergeRequests below
_mergeCount = 0
_mergeId = None


def mergeRequests(builder, req1, req2):
    """
    Returns True if req1 and req2 are mergeable requests, False otherwise.

    This is called by buildbot to determine if pairs of buildrequests can be
    merged together for a build.

    Args:
        builder (buildbot builder object): which builder is being considered
        req1 (buildbot request object): first request being considered.
            This stays constant for a given build being constructed.
        req2 (buildbot request object): second request being considered.
            This changes as the buildbot master considers all pending requests
            for the build.
    """
    global _mergeCount, _mergeId

    # If the requests are fundamentally unmergeable, get that done first
    if not req1.canBeMergedWith(req2):
        # The requests are inherently unmergeable; e.g. on different branches
        # Don't log this; it would be spammy
        return False

    log.msg("mergeRequests: considering %s %s %s" % (builder.name, req1.id, req2.id))
    # Merging is disallowed on these builders
    if builder.name in nomergeBuilders:
        log.msg("mergeRequests: in nomergeBuilders; returning False")
        return False

    if 'Self-serve' in req1.reason or 'Self-serve' in req2.reason:
        # A build was explicitly requested on this revision, so don't coalesce
        # it
        log.msg("mergeRequests: self-serve; returning False")
        return False

    # Disable merging of nightly jobs
    if req1.properties.getProperty('nightly_build', False) or req2.properties.getProperty('nightly_build', False):
        log.msg("mergeRequests: nightly_build; returning False")
        return False

    # We're merging a different request now; reset the state
    # This works because buildbot calls this function with the same req1 for
    # all pending requests for the builder, only req2 varies between calls.
    # Once req1 changes we know we're in the middle of creating a different
    # build.
    if req1.id != _mergeId:
        # Start counting at 1 here, since if we're being called, we've already
        # got 2 requests we're considering merging. If we pa
        _mergeCount = 1
        _mergeId = req1.id
        log.msg("mergeRequests: different r1 id; resetting state")

    if _mergeCount >= builderMergeLimits[builder.name]:
        # This request has already been merged with too many requests
        log.msg("mergeRequests: %s: exceeded limit (%i)" %
                (builder.name, builderMergeLimits[builder.name]))
        return False

    log.msg("mergeRequests: %s merging %i %i" % (builder.name, req1.id, req2.id))
    _mergeCount += 1
    return True


def mergeBuildObjects(d1, d2):
    retval = d1.copy()
    keys = ['builders', 'status', 'schedulers', 'change_source']

    for key in keys:
        retval.setdefault(key, []).extend(d2.get(key, []))

    return retval


def makeMHFactory(config, pf, mh_cfg=None, extra_args=None, **kwargs):
    factory_class = ScriptFactory
    if not mh_cfg:
        mh_cfg = pf['mozharness_config']
    if 'signingServers' in kwargs:
        if kwargs['signingServers'] is not None:
            factory_class = SigningScriptFactory
        else:
            del kwargs['signingServers']

    scriptRepo = config.get('mozharness_repo_url',
                            '%s%s' % (config['hgurl'], config['mozharness_repo_path']))
    script_repo_cache = None
    if config.get('use_mozharness_repo_cache'):  # branch supports it
        script_repo_cache = mh_cfg.get('mozharness_repo_cache',
                                       pf.get('mozharness_repo_cache'))

    if 'env' in pf:
        kwargs['env'] = pf['env'].copy()

    if not extra_args:
        extra_args = mh_cfg.get('extra_args')

    factory = factory_class(
        scriptRepo=scriptRepo,
        interpreter=mh_cfg.get('mozharness_python', pf.get('mozharness_python')),
        scriptName=mh_cfg['script_name'],
        reboot_command=mh_cfg.get('reboot_command', pf.get('reboot_command')),
        extra_args=extra_args,
        script_timeout=mh_cfg.get('script_timeout', pf.get('timeout', 3600)),
        script_maxtime=mh_cfg.get('script_maxtime', pf.get('maxTime', 4 * 3600)),
        script_repo_cache=script_repo_cache,
        script_repo_manifest=config.get('script_repo_manifest'),
        relengapi_archiver_repo_path=config.get('mozharness_archiver_repo_path'),
        relengapi_archiver_rev=config.get('mozharness_archiver_rev'),
        tools_repo_cache=mh_cfg.get('tools_repo_cache',
                                    pf.get('tools_repo_cache')),
        **kwargs
    )
    return factory


def generateTestBuilder(config, branch_name, platform, name_prefix,
                        build_dir_prefix, suites_name, suites,
                        mochitestLeakThreshold, crashtestLeakThreshold,
                        slaves=None, resetHwClock=False, category=None,
                        stagePlatform=None, stageProduct=None,
                        mozharness=False, mozharness_python=None,
                        mozharness_suite_config=None,
                        mozharness_repo=None, mozharness_tag='production',
                        script_repo_manifest=None, relengapi_archiver_repo_path=None,
                        relengapi_archiver_rev=None, is_debug=None):
    # We only support mozharness stuff now!
    assert mozharness
    builders = []
    if slaves is None:
        slavenames = config['platforms'][platform]['slaves']
    else:
        slavenames = slaves
    if not category:
        category = branch_name
    branchProperty = branch_name
    properties = {'branch': branchProperty, 'platform': platform,
                  'slavebuilddir': 'test', 'stage_platform': stagePlatform,
                  'product': stageProduct, 'repo_path': config['repo_path'],
                  'moz_repo_path': config.get('moz_repo_path', '')}
    # suites is a dict!
    if mozharness_suite_config is None:
        mozharness_suite_config = {}
    extra_args = []
    if mozharness_suite_config.get('config_files'):
        extra_args.extend(['--cfg', ','.join(mozharness_suite_config['config_files'])])
    extra_args.extend(mozharness_suite_config.get('extra_args', suites.get('extra_args', [])))
    if is_debug is True:
        extra_args.extend(
            mozharness_suite_config.get(
                'debug_extra_args',
                suites.get('debug_extra_args', [])
            )
        )
    elif is_debug is False:
        extra_args.extend(
            mozharness_suite_config.get(
                'opt_extra_args',
                suites.get('opt_extra_args', [])
            )
        )
    if mozharness_suite_config.get('blob_upload'):
        extra_args.extend(['--blob-upload-branch', branch_name])
    if mozharness_suite_config.get('download_symbols'):
        extra_args.extend(['--download-symbols', mozharness_suite_config['download_symbols']])
    reboot_command = mozharness_suite_config.get(
        'reboot_command', suites.get('reboot_command', None))
    hg_bin = mozharness_suite_config.get(
        'hg_bin', suites.get('hg_bin', 'hg'))
    properties['script_repo_revision'] = mozharness_tag
    factory = ScriptFactory(
        interpreter=mozharness_python,
        scriptRepo=mozharness_repo,
        scriptName=suites['script_path'],
        hg_bin=hg_bin,
        extra_args=extra_args,
        use_credentials_file=True,
        script_maxtime=suites.get('script_maxtime', 7200),
        script_timeout=suites.get('timeout', 1800),
        script_repo_manifest=script_repo_manifest,
        relengapi_archiver_repo_path=relengapi_archiver_repo_path,
        relengapi_archiver_rev=relengapi_archiver_rev,
        reboot_command=reboot_command,
        platform=platform,
        env=mozharness_suite_config.get('env', {}),
        log_eval_func=rc_eval_func({
            0: SUCCESS,
            1: WARNINGS,
            2: FAILURE,
            3: EXCEPTION,
            4: RETRY,
        }),
    )
    builder = {
        'name': '%s %s' % (name_prefix, suites_name),
        'slavenames': slavenames,
        'builddir': '%s-%s' % (build_dir_prefix, suites_name),
        'slavebuilddir': 'test',
        'factory': factory,
        'category': category,
        'properties': properties,
        'nextSlave': _nextAWSSlave_nowait,
    }
    builders.append(builder)
    return builders


def generateMozharnessTalosBuilder(platform, mozharness_repo, script_path,
                                   hg_bin, mozharness_python,
                                   reboot_command, extra_args=None,
                                   script_timeout=3600,
                                   script_maxtime=7200,
                                   script_repo_manifest=None,
                                   relengapi_archiver_repo_path=None,
                                   relengapi_archiver_rev=None):
    if extra_args is None:
        extra_args = []
    return ScriptFactory(
        interpreter=mozharness_python,
        scriptRepo=mozharness_repo,
        scriptName=script_path,
        hg_bin=hg_bin,
        extra_args=extra_args,
        use_credentials_file=True,
        script_timeout=script_timeout,
        script_maxtime=script_maxtime,
        script_repo_manifest=script_repo_manifest,
        relengapi_archiver_repo_path=relengapi_archiver_repo_path,
        relengapi_archiver_rev=relengapi_archiver_rev,
        reboot_command=reboot_command,
        platform=platform,
        log_eval_func=rc_eval_func({
            0: SUCCESS,
            1: WARNINGS,
            2: FAILURE,
            3: EXCEPTION,
            4: RETRY,
        }),
    )


def generateUnittestBuilders(platform_name, branch, test_type,
                             create_pgo_builders, **test_builder_kwargs):
    builders = []
    builders.extend(generateTestBuilder(**test_builder_kwargs))
    if create_pgo_builders and test_type == 'opt':
        pgo_builder_kwargs = test_builder_kwargs.copy()
        pgo_builder_kwargs['name_prefix'] = "%s %s pgo test" % (platform_name, branch)
        pgo_builder_kwargs['build_dir_prefix'] += '_pgo'
        pgo_builder_kwargs['stagePlatform'] += '-pgo'
        builders.extend(generateTestBuilder(**pgo_builder_kwargs))
    return builders


def generateChunkedUnittestBuilders(total_chunks, *args, **kwargs):
    if not total_chunks:
        return generateUnittestBuilders(
            *args, **kwargs
        )
    builders = []
    for i in range(1, total_chunks + 1):
        kwargs_copy = kwargs.copy()
        kwargs_copy['suites_name'] = '%s-%d' % (kwargs_copy['suites_name'], i)
        chunk_args = [
            '--total-chunks', str(total_chunks),
            '--this-chunk', str(i)
        ]
        if 'extra_args' in kwargs_copy['mozharness_suite_config']:
            # Not used any more
            assert False
        elif 'extra_args' in kwargs_copy['suites']:
            kwargs_copy['suites'] = deepcopy(kwargs['suites'])
            kwargs_copy['suites']['extra_args'].extend(chunk_args)
            # We should have only one set of --this-chunk and --total-chunks here
            assert kwargs_copy['suites']['extra_args'].count('--this-chunk') == 1
            assert kwargs_copy['suites']['extra_args'].count('--total-chunks') == 1
        else:
            # Not used any more
            assert False
        builders.extend(generateUnittestBuilders(
            *args, **kwargs_copy
        ))
    return builders


def generateDesktopMozharnessBuilders(name, platform, config, secrets,
                                      l10nNightlyBuilders, builds_created):
    desktop_mh_builders = []

    pf = config['platforms'][platform]

    mh_cfg = pf['mozharness_desktop_build']
    base_extra_args = mh_cfg.get('extra_args', [])
    # let's grab the extra args that are defined at misc level
    branch_and_pool_args = []
    branch_and_pool_args.extend(['--branch', name])
    if config.get('staging'):
        branch_and_pool_args.extend(['--build-pool', 'staging'])
    else:  # this is production
        branch_and_pool_args.extend(['--build-pool', 'production'])
    base_extra_args.extend(branch_and_pool_args)

    base_builder_dir = '%s-%s' % (name, platform)
    # let's assign next_slave here so we only have to change it in
    # this location for mozharness builds if we swap it out again.
    next_slave = _nextAWSSlave_sort

    return_codes_func = rc_eval_func({
        0: SUCCESS,
        1: WARNINGS,
        2: FAILURE,
        3: EXCEPTION,
        4: RETRY,
    })

    # look mom, no buildbot properties needed for desktop
    # mozharness builds!!
    mh_build_properties = {
        # our buildbot master.cfg requires us to at least have
        # these but mozharness doesn't need them
        'branch': name,
        'platform': platform,
        'product': pf['stage_product'],
        'repo_path': config['repo_path'],
        'script_repo_revision': config["mozharness_tag"],
    }
    dep_signing_servers = secrets.get(pf.get('dep_signing_servers'))
    nightly_signing_servers = secrets.get(pf.get('nightly_signing_servers'))

    # grab the l10n schedulers that nightlies will trigger (if any)
    triggered_nightly_schedulers = []
    if config.get("enable_triggered_nightly_scheduler", True):
        if is_l10n_with_mh(config, platform):
            scheduler_name = mh_l10n_scheduler_name(config, platform)
            triggered_nightly_schedulers.append(scheduler_name)
        elif (config['enable_l10n'] and platform in config['l10n_platforms'] and
                '%s nightly' % pf['base_name'] in l10nNightlyBuilders):
            base_name = '%s nightly' % pf['base_name']
            # see bug 1150015
            l10n_builder = l10nNightlyBuilders[base_name]['l10n_builder']
            assert(isinstance(l10n_builder, str))
            triggered_nightly_schedulers.append(l10n_builder)
        elif config['enable_l10n'] and pf.get('is_mobile_l10n') and pf.get('l10n_chunks'):
            triggered_nightly_schedulers.append('%s-%s-l10n' % (name, platform))

    # if we do a generic dep build
    if pf.get('enable_dep', True) or pf.get('enable_periodic', False):
        factory = makeMHFactory(config, pf, mh_cfg=mh_cfg,
                                extra_args=base_extra_args,
                                signingServers=dep_signing_servers,
                                use_credentials_file=True,
                                log_eval_func=return_codes_func)
        generic_builder = {
            'name': '%s build' % pf['base_name'],
            'builddir': base_builder_dir,
            'slavebuilddir': normalizeName(base_builder_dir),
            'slavenames': pf['slaves'],
            'nextSlave': next_slave,
            'factory': factory,
            'category': name,
            'properties': mh_build_properties.copy(),
        }
        desktop_mh_builders.append(generic_builder)
        builds_created['done_generic_build'] = True

    # if do nightly:
    if config['enable_nightly'] and pf.get('enable_nightly', True):

        nightly_extra_args = base_extra_args + config['mozharness_desktop_extra_options']['nightly']
        # include use_credentials_file for balrog step
        nightly_factory = makeMHFactory(config, pf, mh_cfg=mh_cfg,
                                        extra_args=nightly_extra_args,
                                        signingServers=nightly_signing_servers,
                                        triggered_schedulers=triggered_nightly_schedulers,
                                        copy_properties=['buildid', 'builduid'],
                                        use_credentials_file=True,
                                        log_eval_func=return_codes_func)
        nightly_builder = {
            'name': '%s nightly' % pf['base_name'],
            'builddir': '%s-nightly' % base_builder_dir,
            'slavebuilddir': normalizeName('%s-nightly' % base_builder_dir),
            'slavenames': pf['slaves'],
            'nextSlave': next_slave,
            'factory': nightly_factory,
            'category': name,
            'properties': mh_build_properties.copy(),
        }
        desktop_mh_builders.append(nightly_builder)
        builds_created['done_nightly_build'] = True
        if is_l10n_with_mh(config, platform):
            l10n_builders = mh_l10n_builders(config, platform, name, secrets,
                                             is_nightly=True)
            desktop_mh_builders.extend(l10n_builders)
            builds_created['done_l10n_repacks'] = True

    # Handle l10n for try
    elif config.get('enable_try') and config['enable_l10n']:
        if is_l10n_with_mh(config, platform):
            l10n_builders = mh_l10n_builders(config, platform, name, secrets,
                                             is_nightly=False)
            desktop_mh_builders.extend(l10n_builders)
            builds_created['done_l10n_repacks'] = True

    # if we_do_pgo:
    if (config['pgo_strategy'] in ('periodic', 'try') and
            platform in config['pgo_platforms']):
        pgo_extra_args = base_extra_args + config['mozharness_desktop_extra_options']['pgo']
        pgo_factory = makeMHFactory(
            config, pf, mh_cfg=mh_cfg, extra_args=pgo_extra_args,
            use_credentials_file=True,
            signingServers=dep_signing_servers, log_eval_func=return_codes_func
        )
        pgo_builder = {
            'name': '%s pgo-build' % pf['base_name'],
            'builddir': '%s-pgo' % base_builder_dir,
            'slavebuilddir': normalizeName('%s-pgo' % base_builder_dir),
            'slavenames': pf['slaves'],
            'factory': pgo_factory,
            'category': name,
            'nextSlave': next_slave,
            'properties': mh_build_properties.copy(),
        }
        desktop_mh_builders.append(pgo_builder)
        builds_created['done_pgo_build'] = True

    # finally let's return which builders we did so we know what's left to do!
    return desktop_mh_builders


def generateBranchObjects(config, name, secrets=None):
    """name is the name of branch which is usually the last part of the path
       to the repository. For example, 'mozilla-central', 'mozilla-aurora', or
       'mozilla-1.9.1'.
       config is a dictionary containing all of the necessary configuration
       information for a branch. The required keys depends greatly on what's
       enabled for a branch (unittests, l10n, etc). The best way
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
    if secrets is None:
        secrets = {}
    # List of all the per-checkin builders
    builders = []
    # Which builders should we consider when looking at per-checkin results and
    # determining what revision we should do a nightly build on
    buildersForNightly = []
    buildersByProduct = {}
    nightlyBuilders = []
    periodicBuilders = []
    weeklyBuilders = []
    # prettyNames is a mapping to pass to the try_parser for validation
    PRETTY_NAME = '%(basename)s %(trystatus)sbuild'
    NAME = '%(basename)s build'
    prettyNames = {}
    # These dicts provides mapping between en-US dep and nightly scheduler names
    # to l10n dep and l10n nightly scheduler names. It's filled out just below
    # here.
    l10nBuilders = {}
    l10nNightlyBuilders = {}
    pollInterval = config.get('pollInterval', 60)
    l10nPollInterval = config.get('l10nPollInterval', 5 * 60)

    # We only understand a couple PGO strategies
    assert config['pgo_strategy'] in ('per-checkin', 'periodic', 'try', None), \
        "%s is not an understood PGO strategy" % config['pgo_strategy']

    # This section is to make it easier to disable certain products.
    # Ideally we could specify a shorter platforms key on the branch,
    # but that doesn't work
    enabled_platforms = []
    for platform in sorted(config['platforms'].keys()):
        pf = config['platforms'][platform]
        if pf['stage_product'] in config['enabled_products']:
            enabled_platforms.append(platform)

    # generate a list of builders, nightly builders (names must be different)
    # for easy access
    for platform in enabled_platforms:

        pf = config['platforms'][platform]
        base_name = pf['base_name']

        if 'mozharness_config' in pf:
            # this is a spider or b2g build. mozharness desktop builds use same
            # scheduling/naming logic as the non mozharness equivalent.
            if pf.get('enable_dep', True):
                buildername = '%s_dep' % pf['base_name']
                builders.append(buildername)
                if pf.get('consider_for_nightly', True):
                    buildersForNightly.append(buildername)
                buildersByProduct.setdefault(
                    pf['stage_product'], []).append(buildername)
                prettyNames[platform] = buildername
                if not pf.get('try_by_default', True):
                    prettyNames[platform] += " try-nondefault"
            elif pf.get('enable_periodic', False):
                buildername = "%s_periodic" % pf['base_name']
                periodicBuilders.append(buildername)
                if pf.get('consider_for_nightly', True):
                    buildersForNightly.append(buildername)
                if not pf.get('try_by_default', True):
                    prettyNames[platform] += " try-nondefault"

            if pf.get('enable_nightly'):
                buildername = '%s_nightly' % pf['base_name']
                nightlyBuilders.append(buildername)

            continue

        values = {'basename': base_name,
                  'trystatus': '' if pf.get('try_by_default', True) else 'try-nondefault ',
                  }
        pretty_name = PRETTY_NAME % values
        buildername = NAME % values

        if pf.get('enable_dep', True):
            builders.append(buildername)
            if pf.get('consider_for_nightly', True):
                buildersForNightly.append(buildername)
            buildersByProduct.setdefault(
                pf['stage_product'], []).append(buildername)
            prettyNames[platform] = pretty_name
        elif pf.get('enable_periodic', False):
            periodicBuilders.append(buildername)
            if pf.get('consider_for_nightly', True):
                buildersForNightly.append(buildername)

        if config['enable_valgrind'] and \
                platform in config['valgrind_platforms']:
            builders.append('%s valgrind' % base_name)
            buildersByProduct.setdefault(
                pf['stage_product'], []).append('%s valgrind' % base_name)
            prettyNames["%s-valgrind" % platform] = "%s valgrind" % base_name

        # Fill the l10n dep dict
        if config['enable_l10n'] and platform in config['l10n_platforms'] and \
                config['enable_l10n_onchange']:
                l10nBuilders[base_name] = {}
                l10nBuilders[base_name]['tree'] = config['l10n_tree']
                l10nBuilders[base_name]['l10n_builder'] = \
                    '%s %s %s l10n dep' % (pf['product_name'].capitalize(),
                                           name, platform)
                l10nBuilders[base_name]['platform'] = platform
        # Check if branch wants nightly builds
        if config['enable_nightly']:
            if 'enable_nightly' in pf:
                do_nightly = pf['enable_nightly']
            else:
                do_nightly = True
        else:
            do_nightly = False

        # Check if platform as a PGO builder
        if config['pgo_strategy'] in ('periodic',) and platform in config['pgo_platforms']:
            periodicBuilders.append('%s pgo-build' % pf['base_name'])
        elif config['pgo_strategy'] in ('try',) and platform in config['pgo_platforms']:
            builders.append('%s pgo-build' % pf['base_name'])
            buildersByProduct.setdefault(pf['stage_product'], []).append(
                '%s pgo-build' % pf['base_name'])

        if do_nightly:
            builder = '%s nightly' % base_name
            nightlyBuilders.append(builder)
            if config["enable_l10n"]:
                l10n_builder = '%s %s %s l10n nightly' % (
                    pf['product_name'].capitalize(), name, platform
                )

            # Fill the l10nNightly dict
            # trying to do repacks with mozharness
            if is_l10n_with_mh(config, platform):
                # we need this later...
                builder_names = mh_l10n_builder_names(config, platform, branch=name,
                                                      is_nightly=True)
                scheduler_name = mh_l10n_scheduler_name(config, platform)
                l10nNightlyBuilders[builder] = {}
                l10nNightlyBuilders[builder]['l10n_builder'] = builder_names
                l10nNightlyBuilders[builder]['platform'] = platform
                l10nNightlyBuilders[builder]['scheduler_name'] = scheduler_name
                l10nNightlyBuilders[builder]['l10n_repacks_with_mh'] = True
            else:
                # no repacks with mozharness, old style repacks
                if config['enable_l10n'] and platform in config['l10n_platforms']:
                    l10nNightlyBuilders[builder] = {}
                    l10nNightlyBuilders[builder]['tree'] = config['l10n_tree']
                    l10nNightlyBuilders[builder]['l10n_builder'] = l10n_builder
                    l10nNightlyBuilders[builder]['platform'] = platform
        if platform in ('linux64',):
            if config.get('enable_blocklist_update', False) or \
               config.get('enable_hsts_update', False) or \
               config.get('enable_hpkp_update', False):
                weeklyBuilders.append('%s periodic file update' % base_name)

    # Try Server notifier
    if config.get('enable_mail_notifier'):
        packageUrl = config['package_url']
        packageDir = config['package_dir']

        if config.get('notify_real_author'):
            extraRecipients = []
            sendToInterestedUsers = True
        else:
            extraRecipients = config['email_override']
            sendToInterestedUsers = False

        # This notifies users as soon as we receive their push, and will let them
        # know where to find builds/logs
        branchObjects['status'].append(ChangeNotifier(
            fromaddr="tryserver@build.mozilla.org",
            lookup=MercurialEmailLookup(),
            sendToInterestedUsers=sendToInterestedUsers,
            extraRecipients=extraRecipients,
            branches=[config['repo_path']],
            messageFormatter=lambda c: buildTryChangeMessage(c,
                                                             '/'.join([packageUrl, packageDir])),
        ))

    tipsOnly = False
    maxChanges = 100
    if config.get('enable_try', False):
        # Pay attention to all branches for pushes to try
        repo_branch = None
    else:
        # Other branches should only pay attention to the default branch
        repo_branch = "default"

    branchObjects['change_source'].append(HgPoller(
        hgURL=config['hgurl'],
        branch=config.get("poll_repo", config['repo_path']),
        tipsOnly=tipsOnly,
        maxChanges=maxChanges,
        repo_branch=repo_branch,
        pollInterval=pollInterval,
    ))

    if config['enable_l10n'] and config['enable_l10n_onchange']:
        hg_all_locales_poller = HgAllLocalesPoller(hgURL=config['hgurl'],
                                                   repositoryIndex=config[
                                                   'l10n_repo_path'],
                                                   pollInterval=l10nPollInterval,
                                                   branch=name)
        hg_all_locales_poller.parallelRequests = 1
        branchObjects['change_source'].append(hg_all_locales_poller)

    # schedulers
    # this one gets triggered by the HG Poller
    # for Try we have a custom scheduler that can accept a function to read commit comments
    # in order to know what to schedule
    extra_args = {}
    extra_args['treeStableTimer'] = None
    if config.get('enable_try'):
        scheduler_class = makePropertiesScheduler(
            BuilderChooserScheduler, [buildUIDSchedFunc])
        extra_args['chooserFunc'] = tryChooser
        extra_args['prettyNames'] = prettyNames
        extra_args['buildbotBranch'] = name
    else:
        scheduler_class = makePropertiesScheduler(
            Scheduler, [buildIDSchedFunc, buildUIDSchedFunc])

    if not config.get('enable_merging', True) or not config.get('merge_builds', True):
        nomergeBuilders.update(builders)
    # these should never, ever merge
    nomergeBuilders.update(periodicBuilders)

    if 'product_prefix' in config:
        scheduler_name_prefix = "%s_%s" % (config['product_prefix'], name)
    else:
        scheduler_name_prefix = name

    if config.get("enable_onchange_scheduler", True):
        for product, product_builders in buildersByProduct.iteritems():
            if config.get('enable_try'):
                fileIsImportant = lambda c: isHgPollerTriggered(c, config['hgurl'])
            else:
                # The per-product build behaviour is tweakable per branch, and
                # by default is opt-out. (Bug 1056792).
                if not config.get('enable_perproduct_builds', True):
                    fileIsImportant = makeImportantFunc(config['hgurl'], None)
                else:
                    fileIsImportant = makeImportantFunc(config['hgurl'], product)

            branchObjects['schedulers'].append(scheduler_class(
                name=scheduler_name_prefix + "-" + product,
                branch=config.get("poll_repo", config['repo_path']),
                builderNames=product_builders,
                fileIsImportant=fileIsImportant,
                **extra_args
            ))

    if config['enable_l10n'] and config.get("enable_l10n_dep_scheduler", True):
        l10n_builders = []
        for b in l10nBuilders:
            l10n_builders.append(l10nBuilders[b]['l10n_builder'])
        nomergeBuilders.update(l10n_builders)
        # This L10n scheduler triggers only the builders of its own branch
        branchObjects['schedulers'].append(Scheduler(
            name="%s l10n" % name,
            branch=config['l10n_repo_path'],
            treeStableTimer=None,
            builderNames=l10n_builders,
            fileIsImportant=lambda c: isImportantL10nFile(
                c, config['l10n_modules']),
            properties={
                'app': 'browser',
                'en_revision': 'default',
            }
        ))

    # Now, setup the nightly en-US schedulers and maybe,
    # their downstream l10n ones
    if nightlyBuilders and config.get("enable_nightly_scheduler", True):
        if config.get('enable_nightly_lastgood', False):
            goodFunc = lastGoodFunc(
                branch=config['repo_path'],
                builderNames=buildersForNightly,
                triggerBuildIfNoChanges=False,
                l10nBranch=config.get('l10n_repo_path')
            )
        elif config.get('enable_nightly_everytime', True):
            goodFunc = lastRevFunc(
                config['repo_path'], triggerBuildIfNoChanges=True)
        else:
            goodFunc = lastRevFunc(
                config['repo_path'], triggerBuildIfNoChanges=False)

        nightly_scheduler = makePropertiesScheduler(
            SpecificNightly,
            [buildIDSchedFunc, buildUIDSchedFunc])(
                name="%s nightly" % scheduler_name_prefix,
                ssFunc=goodFunc,
                branch=config['repo_path'],
                # bug 482123 - keep the minute to avoid problems with DST
                # changes
                hour=config['start_hour'], minute=config['start_minute'],
                builderNames=nightlyBuilders,
            )
        branchObjects['schedulers'].append(nightly_scheduler)

    if len(periodicBuilders) > 0 and config.get("enable_periodic_scheduler", True):
        hour = config['periodic_start_hours']
        minute = config.get('periodic_start_minute', 0)

        periodic_scheduler = makePropertiesScheduler(
            SpecificNightly,
            [buildIDSchedFunc, buildUIDSchedFunc])(
                ssFunc=lastRevFunc(config['repo_path'],
                                   triggerBuildIfNoChanges=False),
                name="%s periodic" % scheduler_name_prefix,
                branch=config['repo_path'],
                builderNames=periodicBuilders,
                hour=hour,
                minute=minute,
            )
        branchObjects['schedulers'].append(periodic_scheduler)

    for builder in nightlyBuilders:
        # looping through l10n builders

        if builder in l10nNightlyBuilders and \
           'l10n_repacks_with_mh' in l10nNightlyBuilders[builder]:
            # it's a repacks with mozharness
            l10n_builders = l10nNightlyBuilders[builder]['l10n_builder']
            nomergeBuilders.update(l10n_builders)
            if config.get("enable_triggered_nightly_scheduler", True):
                triggerable_name = l10nNightlyBuilders[builder]['scheduler_name']
                triggerable = Triggerable(name=triggerable_name,
                                        builderNames=l10n_builders)
                branchObjects['schedulers'].append(triggerable)

        elif config['enable_l10n'] and \
                config['enable_nightly'] and builder in l10nNightlyBuilders:
            # classic repacks
            l10n_builder = l10nNightlyBuilders[builder]['l10n_builder']
            nomergeBuilders.add(l10n_builder)
            if config.get("enable_triggered_nightly_scheduler", True):
                platform = l10nNightlyBuilders[builder]['platform']
                branchObjects['schedulers'].append(TriggerableL10n(
                                                name=l10n_builder,
                                                platform=platform,
                                                builderNames=[l10n_builder],
                                                branch=config['repo_path'],
                                                baseTag='default',
                                                localesURL=config.get(
                                                    'localesURL', None)
                                                ))

    if weeklyBuilders and config.get("enable_weekly_scheduler", True):
        weekly_scheduler = Nightly(
            name='weekly-%s' % scheduler_name_prefix,
            branch=config['repo_path'],
            dayOfWeek=5,  # Saturday
            hour=[3], minute=[02],
            builderNames=weeklyBuilders,
        )
        branchObjects['schedulers'].append(weekly_scheduler)

    # We iterate throught the platforms a second time, so we need
    # to ensure that disabled platforms aren't configured the second time
    enabled_platforms = []
    for platform in sorted(config['platforms'].keys()):
        pf = config['platforms'][platform]
        if pf['stage_product'] in config['enabled_products']:
            enabled_platforms.append(platform)

    for platform in enabled_platforms:
        # shorthand
        pf = config['platforms'][platform]

        # TODO still need to impl mozharness desktop: try, valgrind
        # etc builders
        # For now, let's just record when we create desktop builds like
        # generic, pgo, and nightly via mozharness and fall back to buildbot
        # for other builder factory logic  (outside the mozharness_config
        # condition)
        # NOTE: when we no longer need to fall back for remaining builders,
        # we will not need these booleans
        builder_tracker = {
            'done_generic_build': False,  # this is the basic pf
            'done_pgo_build': False,  # generic pf + pgo
            'done_nightly_build': False,  # generic pf + nightly
            'done_l10n_repacks': False,  # generic pf + l10n
        }

        if 'mozharness_desktop_build' in pf:
            # platform is a desktop pf and is able to do use mozharness
            if config.get('desktop_mozharness_builds_enabled'):
                # branch has desktop mozharness builds enabled
                branchObjects['builders'].extend(
                    generateDesktopMozharnessBuilders(
                        name, platform, config, secrets,
                        l10nNightlyBuilders, builds_created=builder_tracker)
                )
            # now crawl outside this condition and see what builders are left
            #  to do in MBF land
            # *NOTE: once we implement everything, we do not need to check below
            pass  # keep going
        elif 'mozharness_config' in pf:
            # this is not a desktop mozharness build
            # e.g. it could be b2g or spider)

            # at the end of this block we 'continue' because we have
            # finished all the builders needed for this platform and
            # there is nothing left to do
            if 'mozharness_repo_url' in pf:
                config['mozharness_repo_url'] = pf['mozharness_repo_url']

            factory = makeMHFactory(config, pf,
                                    signingServers=secrets.get(pf.get('dep_signing_servers')),
                                    use_credentials_file=True)
            builder = {
                'name': '%s_dep' % pf['base_name'],
                'slavenames': pf['slaves'],
                'nextSlave': _nextAWSSlave_sort,
                'builddir': '%s_dep' % pf['base_name'],
                'slavebuilddir': normalizeName('%s_dep' % pf['base_name']),
                'factory': factory,
                'category': name,
                'properties': {
                    'branch': name,
                    'platform': platform,
                    'product': pf['stage_product'],
                    'repo_path': config['repo_path'],
                    'script_repo_revision': pf.get('mozharness_tag', config['mozharness_tag']),
                    'hgurl': config.get('hgurl'),
                    'base_mirror_urls': config.get('base_mirror_urls'),
                    'base_bundle_urls': config.get('base_bundle_urls'),
                    'tooltool_url_list': config.get('tooltool_url_list'),
                    'mock_target': pf.get('mock_target'),
                    'upload_ssh_server': config.get('stage_server'),
                    'upload_ssh_user': config.get('stage_username'),
                    'upload_ssh_key': config.get('stage_ssh_key'),
                }
            }
            if pf.get('enable_dep', True):
                branchObjects['builders'].append(builder)
            elif pf.get('enable_periodic', False):
                builder['name'] = '%s_periodic' % pf['base_name']
                branchObjects['builders'].append(builder)

            if pf.get('enable_nightly'):
                if pf.get('dep_signing_servers') != pf.get('nightly_signing_servers'):
                    # We need a new factory for this because our signing
                    # servers are different
                    factory = makeMHFactory(config, pf, signingServers=secrets.get(pf.get('nightly_signing_servers')),
                                            use_credentials_file=True)

                nightly_builder = {
                    'name': '%s_nightly' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'nextSlave': _nextAWSSlave_sort,
                    'builddir': '%s_nightly' % pf['base_name'],
                    'slavebuilddir': normalizeName('%s_nightly' % pf['base_name']),
                    'factory': factory,
                    'category': name,
                    'properties': builder['properties'].copy(),
                }
                nightly_builder['properties']['nightly_build'] = True
                branchObjects['builders'].append(nightly_builder)
            # Nothing else to do for this builder
            continue

        # The stage platform needs to be used by the factory __init__ methods
        # as well as the log handler status target.  Instead of repurposing the
        # platform property on each builder, we will create a new property
        # on the needed builders
        stage_platform = pf.get('stage_platform', platform)

        uploadPackages = pf.get('uploadPackages', True)
        uploadSymbols = False
        disableSymbols = pf.get('disable_symbols', False)
        talosMasters = pf.get('talos_masters', [])
        unittestBranch = "%s-%s-unittest" % (name, pf.get('unittest_platform', platform))
        # Generate the PGO branch even if it isn't on for dep builds
        # because we will still use it for nightlies... maybe
        pgoUnittestBranch = "%s-%s-pgo-unittest" % (name, platform)
        tinderboxBuildsDir = None

        # Allow for test packages on platforms that can't be tested
        # on the same master.
        packageTests = pf.get('packageTests', False)

        doBuildAnalysis = pf.get('enable_build_analysis', False)

        buildSpace = pf.get('build_space', config['default_build_space'])
        l10nSpace = config['default_l10n_space']
        clobberTime = pf.get('clobber_time', config['default_clobber_time'])
        checkTest = pf.get('enable_checktests', False)
        valgrindCheck = pf.get('enable_valgrind_checktests', False)
        # Turn pymake on by default for Windows, and off by default for
        # other platforms.
        if 'win' in platform:
            enable_pymake = pf.get('enable_pymake', True)
        else:
            enable_pymake = pf.get('enable_pymake', False)

        extra_args = {}
        if config.get('enable_try'):
            factory_class = TryBuildFactory
            extra_args['packageUrl'] = config['package_url']
            extra_args['packageDir'] = config['package_dir']
            extra_args['branchName'] = name
            uploadSymbols = pf.get('upload_symbols', False)
        else:
            factory_class = NightlyBuildFactory
            uploadSymbols = False

        stageBasePath = '%s/%s' % (config['stage_base_path'],
                                   pf['stage_product'])

        # For the 'per-checkin' pgo strategy, we want PGO
        # enabled on what would be 'opt' builds.
        if platform in config['pgo_platforms']:
            if config['pgo_strategy'] in ('periodic', 'try') or config['pgo_strategy'] is None:
                per_checkin_build_uses_pgo = False
            elif config['pgo_strategy'] == 'per-checkin':
                per_checkin_build_uses_pgo = True
        else:
            # All platforms that can't do PGO... shouldn't do PGO.
            per_checkin_build_uses_pgo = False

        if per_checkin_build_uses_pgo:
            per_checkin_unittest_branch = pgoUnittestBranch
        else:
            per_checkin_unittest_branch = unittestBranch

        if config.get('mozilla_dir'):
            extra_args['mozillaDir'] = config['mozilla_dir']
        if config.get('mozilla_srcdir'):
            extra_args['mozillaSrcDir'] = config['mozilla_srcdir']

        multiargs = {}
        if pf.get('product_name') == 'b2g':
            multiargs[
                'multiLocaleScript'] = 'scripts/b2g_desktop_multilocale.py'
            # b2g builds require mozharness
            multiargs['mozharnessRepoPath'] = config.get('mozharness_repo_path')
        else:
            if 'android' in platform:
                multiargs['multiLocaleScript'] = 'scripts/multil10n.py'
                # android nightlies require mozharness
                multiargs['mozharnessRepoPath'] = config.get('mozharness_repo_path')
        if pf.get('multi_config_name'):
            multiargs['multiLocaleConfig'] = pf['multi_config_name']
        else:
            if pf.get('multi_locale_config_platform'):
                # normally we look for the mozharness config by platform. But since we have split
                # 'android' into two platforms 'android-api-9' and 'android-api-10', this allows us
                # to use the already existing '{branch}_android.json' config files for both  without
                # having to create a dozen new duplicate ones
                multi_config_pf = pf['multi_locale_config_platform']
            else:
                multi_config_pf = platform
            multiargs['multiLocaleConfig'] = 'multi_locale/%s_%s.json' % (
                name, multi_config_pf)
        if config.get('enable_multi_locale') and pf.get('multi_locale'):
            multiargs['multiLocale'] = True
            multiargs['multiLocaleMerge'] = config['multi_locale_merge']
            multiargs['compareLocalesRepoPath'] = config[
                'compare_locales_repo_path']
            multiargs['compareLocalesTag'] = config['compare_locales_tag']
            multiargs['mozharnessMultiOptions'] = pf.get(
                'mozharness_multi_options')

        mozharness_repo_cache = None
        if config.get('use_mozharness_repo_cache'):  # branch supports it
            mozharness_repo_cache = pf.get('mozharness_repo_cache')

        # Some platforms shouldn't do dep builds
        if pf.get('enable_dep', True) or pf.get('enable_periodic', False):
            # This condition just checks to see if we used
            # mozharness to create this builder already. Once we port all
            # builders to mozharness we won't need mozilla2_dep_builder at
            # all
            if not builder_tracker['done_generic_build']:
                factory_kwargs = {
                    'env': pf['env'],
                    'objdir': pf['platform_objdir'],
                    'platform': platform,
                    'hgHost': config['hghost'],
                    'repoPath': config['repo_path'],
                    'buildToolsRepoPath': config['build_tools_repo_path'],
                    'configRepoPath': config['config_repo_path'],
                    'profiledBuild': per_checkin_build_uses_pgo,
                    'productName': pf['product_name'],
                    'mozconfig': pf['mozconfig'],
                    'srcMozconfig': pf.get('src_mozconfig'),
                    'use_mock': pf.get('use_mock'),
                    'mock_target': pf.get('mock_target'),
                    'mock_packages': pf.get('mock_packages'),
                    'mock_copyin_files': pf.get('mock_copyin_files'),
                    'stageServer': config['stage_server'],
                    'stageUsername': config['stage_username'],
                    'stageGroup': config['stage_group'],
                    'stageSshKey': config['stage_ssh_key'],
                    'stageBasePath': stageBasePath,
                    'stageLogBaseUrl': config.get('stage_log_base_url', None),
                    'stagePlatform': pf['stage_platform'],
                    'stageProduct': pf['stage_product'],
                    'graphServer': config['graph_server'],
                    'graphSelector': config['graph_selector'],
                    'graphBranch': config.get('graph_branch', config.get('tinderbox_tree', None)),
                    'doBuildAnalysis': doBuildAnalysis,
                    'baseName': pf['base_name'],
                    'checkTest': checkTest,
                    'valgrindCheck': valgrindCheck,
                    'uploadPackages': uploadPackages,
                    'uploadSymbols': uploadSymbols,
                    'disableSymbols': disableSymbols,
                    'buildSpace': buildSpace,
                    'clobberURL': config['base_clobber_url'],
                    'clobberTime': clobberTime,
                    'buildsBeforeReboot': pf['builds_before_reboot'],
                    'talosMasters': talosMasters,
                    'enablePackaging': pf.get('enable_packaging', True),
                    'enableInstaller': pf.get('enable_installer', False),
                    'packageTests': packageTests,
                    'unittestMasters': pf.get('unittest_masters', config['unittest_masters']),
                    'unittestBranch': per_checkin_unittest_branch,
                    'tinderboxBuildsDir': tinderboxBuildsDir,
                    'enable_ccache': pf.get('enable_ccache', False),
                    'useSharedCheckouts': pf.get('enable_shared_checkouts', False),
                    'testPrettyNames': pf.get('test_pretty_names', False),
                    'l10nCheckTest': pf.get('l10n_check_test', False),
                    'post_upload_include_platform': pf.get('post_upload_include_platform', False),
                    'signingServers': secrets.get(pf.get('dep_signing_servers')),
                    'baseMirrorUrls': config.get('base_mirror_urls'),
                    'baseBundleUrls': config.get('base_bundle_urls'),
                    'mozillaDir': config.get('mozilla_dir', None),
                    'mozillaSrcDir': config.get('mozilla_srcdir', None),
                    'tooltool_manifest_src': pf.get('tooltool_manifest_src'),
                    'tooltool_script': pf.get('tooltool_script'),
                    'tooltool_url_list': config.get('tooltool_url_list', []),
                    'gaiaRepo': pf.get('gaia_repo'),
                    'gaiaRevision': config.get('gaia_revision'),
                    'gaiaRevisionFile': pf.get('gaia_revision_file'),
                    'gaiaLanguagesFile': pf.get('gaia_languages_file'),
                    'gaiaLanguagesScript': pf.get('gaia_languages_script', 'scripts/b2g_desktop_multilocale.py'),
                    'gaiaL10nRoot': config.get('gaia_l10n_root'),
                    'mozharness_repo_cache': mozharness_repo_cache,
                    'tools_repo_cache': pf.get('tools_repo_cache'),
                    'mozharnessTag': config.get('mozharness_tag'),
                    'geckoL10nRoot': config.get('gecko_l10n_root'),
                    'geckoLanguagesFile': pf.get('gecko_languages_file'),
                    'enable_pymake': enable_pymake,
                }
                factory_kwargs.update(extra_args)
                if pf.get('product_name') == 'b2g':
                    factory_kwargs.update(multiargs)

                mozilla2_dep_factory = factory_class(**factory_kwargs)
                # eg. TB Linux comm-central build
                #    TB Linux comm-central leak test build
                mozilla2_dep_builder = {
                    'name': '%s build' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s' % (name, platform),
                    'slavebuilddir': normalizeName('%s-%s' % (name, platform), pf['stage_product']),
                    'factory': mozilla2_dep_factory,
                    'category': name,
                    'nextSlave': _nextAWSSlave_sort,
                    'properties': {'branch': name,
                                   'platform': platform,
                                   'stage_platform': stage_platform,
                                   'product': pf['stage_product'],
                                   'slavebuilddir': normalizeName('%s-%s' % (name, platform), pf['stage_product'])},
                }
                branchObjects['builders'].append(mozilla2_dep_builder)

            # We have some platforms which need to be built every X hours with PGO.
            # These builds are as close to regular dep builds as we can make them,
            # other than PGO
            # builder_tracker just checks to see if we used
            # mozharness to create this builder already. Once we port all
            # builders to mozharness we won't need pgo_builder at
            # all
            if (config['pgo_strategy'] in ('periodic', 'try') and
                    platform in config['pgo_platforms'] and not
                    builder_tracker['done_pgo_build']):
                pgo_kwargs = factory_kwargs.copy()
                pgo_kwargs['profiledBuild'] = True
                pgo_kwargs['stagePlatform'] += '-pgo'
                pgo_kwargs['unittestBranch'] = pgoUnittestBranch
                pgo_factory = factory_class(**pgo_kwargs)
                pgo_builder = {
                    'name': '%s pgo-build' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s-pgo' % (name, platform),
                    'slavebuilddir': normalizeName('%s-%s-pgo' % (name, platform), pf['stage_product']),
                    'factory': pgo_factory,
                    'category': name,
                    'nextSlave': _nextAWSSlave_sort,
                    'properties': {'branch': name,
                                   'platform': platform,
                                   'stage_platform': stage_platform + '-pgo',
                                   'product': pf['stage_product'],
                                   'slavebuilddir': normalizeName('%s-%s-pgo' % (name, platform), pf['stage_product'])},
                }
                branchObjects['builders'].append(pgo_builder)

        # skip nightlies for debug builds unless requested at platform level
        if platform.endswith("-debug") and not pf.get('enable_nightly'):
                continue

        if config['enable_nightly']:
            if 'enable_nightly' in pf:
                do_nightly = pf['enable_nightly']
            else:
                do_nightly = True
        else:
            do_nightly = False

        l10n_objdir = pf['platform_objdir']
        if do_nightly:
            nightly_builder = '%s nightly' % pf['base_name']

            platform_env = pf['env'].copy()
            if config.get('update_channel') and config.get('updates_enabled'):
                platform_env['MOZ_UPDATE_CHANNEL'] = config['update_channel']

            triggeredSchedulers = None
            if config['enable_l10n'] and pf.get('is_mobile_l10n') and pf.get('l10n_chunks'):
                mobile_l10n_scheduler_name = '%s-%s-l10n' % (name, platform)
                mobile_l10n_builders = []
                builder_env = platform_env.copy()
                for n in range(1, int(pf['l10n_chunks']) + 1):
                    builddir = '%s-%s-l10n_%s' % (name, platform, str(n))
                    builderName = "%s l10n nightly-%s" % (pf['base_name'], n)
                    mobile_l10n_builders.append(builderName)
                    extra_args = ['--cfg',
                                  config['mozharness_configs']['single_locale_environment'],
                                  '--cfg',
                                  'single_locale/%s_%s.py' % (name, platform),
                                  '--cfg',
                                  config['mozharness_configs']['balrog'],
                                  '--total-chunks', str(pf['l10n_chunks']),
                                  '--this-chunk', str(n)]
                    signing_servers = secrets.get(
                        pf.get('nightly_signing_servers'))
                    factory = SigningScriptFactory(
                        signingServers=signing_servers,
                        scriptRepo='%s%s' % (config['hgurl'],
                                             config['mozharness_repo_path']),
                        scriptName='scripts/mobile_l10n.py',
                        use_credentials_file=True,
                        extra_args=extra_args,
                        relengapi_archiver_repo_path=config.get('mozharness_archiver_repo_path'),
                        relengapi_archiver_rev=config.get('mozharness_archiver_rev'),
                    )
                    slavebuilddir = normalizeName(builddir, pf['stage_product'])
                    branchObjects['builders'].append({
                        'name': builderName,
                        'slavenames': pf.get('slaves'),
                        'builddir': builddir,
                        'slavebuilddir': slavebuilddir,
                        'factory': factory,
                        'category': name,
                        'nextSlave': _nextAWSSlave_sort,
                        'properties': {'branch': name,
                                       'builddir': '%s-l10n_%s' % (builddir, str(n)),
                                       'stage_platform': stage_platform,
                                       'product': pf['stage_product'],
                                       'platform': platform,
                                       'slavebuilddir': slavebuilddir,
                                       'script_repo_revision': config['mozharness_tag'],
                                       'repo_path': config['repo_path'],
                                       },
                        'env': builder_env
                    })

                if config.get("enable_triggered_nightly_scheduler", True):
                    branchObjects["schedulers"].append(Triggerable(
                        name=mobile_l10n_scheduler_name,
                        builderNames=mobile_l10n_builders
                    ))
                    triggeredSchedulers = [mobile_l10n_scheduler_name]

            else:  # Non-mobile l10n is done differently at this time
                if config.get("enable_triggered_nightly_scheduler", True):
                    if config['enable_l10n'] and platform in config['l10n_platforms'] and \
                            nightly_builder in l10nNightlyBuilders:
                        triggeredSchedulers = [
                            l10nNightlyBuilders[nightly_builder]['l10n_builder']]

            nightly_kwargs = {}
            nightly_kwargs.update(multiargs)

            # Platform can override branch config
            updates_enabled = pf.get(
                'updates_enabled', config.get('updates_enabled'))

            if updates_enabled:
                nightly_kwargs.update({
                    'downloadBaseURL': config['download_base_url'],
                })

            # We make the assumption that *all* nightly builds
            # are to be done with PGO.  This is to ensure that all
            # branches get some PGO coverage
            # We do not stick '-pgo' in the stage_platform for
            # nightlies because it'd be ugly and break stuff
            if platform in config['pgo_platforms']:
                nightly_pgo = True
                nightlyUnittestBranch = pgoUnittestBranch
            else:
                nightlyUnittestBranch = unittestBranch
                nightly_pgo = False

            nightly_env = platform_env.copy()
            nightly_env['IS_NIGHTLY'] = "yes"

            # This condition just checks to see if we used
            # mozharness to create this builder already. Once we port all
            # builders to mozharness we won't need mozilla2_nightly_builder at
            # all
            if not builder_tracker['done_nightly_build']:
                mozilla2_nightly_factory = NightlyBuildFactory(
                    env=nightly_env,
                    objdir=pf['platform_objdir'],
                    platform=platform,
                    hgHost=config['hghost'],
                    repoPath=config['repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    configRepoPath=config['config_repo_path'],
                    profiledBuild=nightly_pgo,
                    productName=pf['product_name'],
                    mozconfig=pf['mozconfig'],
                    srcMozconfig=pf.get('src_mozconfig'),
                    use_mock=pf.get('use_mock'),
                    mock_target=pf.get('mock_target'),
                    mock_packages=pf.get('mock_packages'),
                    mock_copyin_files=pf.get('mock_copyin_files'),
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageGroup=config['stage_group'],
                    stageSshKey=config['stage_ssh_key'],
                    stageBasePath=stageBasePath,
                    stageLogBaseUrl=config.get('stage_log_base_url', None),
                    stagePlatform=pf['stage_platform'],
                    stageProduct=pf['stage_product'],
                    doBuildAnalysis=doBuildAnalysis,
                    uploadPackages=uploadPackages,
                    uploadSymbols=pf.get('upload_symbols', False),
                    disableSymbols=pf.get('disable_symbols', False),
                    nightly=True,
                    updates_enabled=updates_enabled,
                    createPartial=pf.get(
                        'create_partial', config['create_partial']),
                    updatePlatform=pf['update_platform'],
                    hashType=config['hash_type'],
                    balrog_api_root=config.get('balrog_api_root', None),
                    balrog_submitter_extra_args=config.get('balrog_submitter_extra_args', None),
                    balrog_credentials_file=config['balrog_credentials_file'],
                    balrog_username=config['balrog_username'],
                    buildSpace=buildSpace,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                    buildsBeforeReboot=pf['builds_before_reboot'],
                    talosMasters=talosMasters,
                    enablePackaging=pf.get('enable_packaging', True),
                    enableInstaller=pf.get('enable_installer', False),
                    packageTests=packageTests,
                    unittestMasters=pf.get(
                        'unittest_masters', config['unittest_masters']),
                    unittestBranch=nightlyUnittestBranch,
                    triggerBuilds=config['enable_l10n'],
                    triggeredSchedulers=triggeredSchedulers,
                    tinderboxBuildsDir=tinderboxBuildsDir,
                    enable_ccache=pf.get('enable_ccache', False),
                    useSharedCheckouts=pf.get('enable_shared_checkouts', False),
                    testPrettyNames=pf.get('test_pretty_names', False),
                    checkTest=pf.get('enable_checktests', False),
                    l10nCheckTest=pf.get('l10n_check_test', False),
                    post_upload_include_platform=pf.get(
                        'post_upload_include_platform', False),
                    signingServers=secrets.get(pf.get('nightly_signing_servers')),
                    baseMirrorUrls=config.get('base_mirror_urls'),
                    baseBundleUrls=config.get('base_bundle_urls'),
                    mozillaDir=config.get('mozilla_dir', None),
                    mozillaSrcDir=config.get('mozilla_srcdir', None),
                    tooltool_manifest_src=pf.get('tooltool_manifest_src'),
                    tooltool_script=pf.get('tooltool_script'),
                    tooltool_url_list=config.get('tooltool_url_list', []),
                    gaiaRepo=pf.get('gaia_repo'),
                    gaiaRevision=config.get('gaia_revision'),
                    gaiaRevisionFile=pf.get('gaia_revision_file'),
                    gaiaLanguagesFile=pf.get('gaia_languages_file'),
                    gaiaLanguagesScript=pf.get('gaia_languages_script',
                                               'scripts/b2g_desktop_multilocale.py'),
                    gaiaL10nRoot=config.get('gaia_l10n_root'),
                    mozharnessTag=config.get('mozharness_tag'),
                    geckoL10nRoot=config.get('gecko_l10n_root'),
                    geckoLanguagesFile=pf.get('gecko_languages_file'),
                    enable_pymake=enable_pymake,
                    mozharness_repo_cache=mozharness_repo_cache,
                    tools_repo_cache=pf.get('tools_repo_cache'),
                    **nightly_kwargs
                )

                # eg. TB Linux comm-aurora nightly
                mozilla2_nightly_builder = {
                    'name': nightly_builder,
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s-nightly' % (name, platform),
                    'slavebuilddir': normalizeName('%s-%s-nightly' % (name, platform), pf['stage_product']),
                    'factory': mozilla2_nightly_factory,
                    'category': name,
                    'nextSlave': _nextAWSSlave_sort,
                    'properties': {'branch': name,
                                   'platform': platform,
                                   'stage_platform': stage_platform,
                                   'product': pf['stage_product'],
                                   'nightly_build': True,
                                   'slavebuilddir': normalizeName(
                                       '%s-%s-nightly' % (name, platform),
                                       pf['stage_product']
                                   )},
                }
                branchObjects['builders'].append(mozilla2_nightly_builder)

            if config['enable_l10n'] and \
               not builder_tracker['done_l10n_repacks']:
                if platform in config['l10n_platforms']:
                    mozconfig = os.path.join(os.path.dirname(
                        pf['src_mozconfig']), 'l10n-mozconfig')
                    l10n_kwargs = {}
                    if config.get('call_client_py', False):
                        l10n_kwargs['callClientPy'] = True
                        l10n_kwargs['clientPyConfig'] = {
                            'chatzilla_repo_path': config.get('chatzilla_repo_path', ''),
                            'cvsroot': config.get('cvsroot', ''),
                            'inspector_repo_path': config.get('inspector_repo_path', ''),
                            'moz_repo_path': config.get('moz_repo_path', ''),
                            'skip_blank_repos': config.get('skip_blank_repos', False),
                            'venkman_repo_path': config.get('venkman_repo_path', ''),
                        }

                    mozilla2_l10n_nightly_factory = NightlyRepackFactory(
                        env=platform_env,
                        objdir=l10n_objdir,
                        platform=platform,
                        hgHost=config['hghost'],
                        tree=config['l10n_tree'],
                        project=pf['product_name'],
                        appName=pf['app_name'],
                        enUSBinaryURL=config['enUS_binaryURL'],
                        nightly=True,
                        mozconfig=mozconfig,
                        l10nNightlyUpdate=config['l10nNightlyUpdate'],
                        l10nDatedDirs=config['l10nDatedDirs'],
                        createPartial=pf.get(
                            'create_partial_l10n', config['create_partial_l10n']),
                        updatePlatform=pf['update_platform'],
                        downloadBaseURL=config['download_base_url'],
                        balrog_api_root=config.get('balrog_api_root', None),
                        balrog_submitter_extra_args=config.get('balrog_submitter_extra_args', None),
                        balrog_credentials_file=config[
                            'balrog_credentials_file'],
                        balrog_username=config['balrog_username'],
                        hashType=config['hash_type'],
                        stageServer=config['stage_server'],
                        stageUsername=config['stage_username'],
                        stageSshKey=config['stage_ssh_key'],
                        repoPath=config['repo_path'],
                        l10nRepoPath=config['l10n_repo_path'],
                        buildToolsRepoPath=config['build_tools_repo_path'],
                        compareLocalesRepoPath=config[
                            'compare_locales_repo_path'],
                        compareLocalesTag=config['compare_locales_tag'],
                        buildSpace=l10nSpace,
                        clobberURL=config['base_clobber_url'],
                        clobberTime=clobberTime,
                        mozillaDir=config.get('mozilla_dir', None),
                        mozillaSrcDir=config.get('mozilla_srcdir', None),
                        signingServers=secrets.get(
                            pf.get('nightly_signing_servers')),
                        baseMirrorUrls=config.get('base_mirror_urls'),
                        extraConfigureArgs=config.get(
                            'l10n_extra_configure_args', []),
                        buildsBeforeReboot=pf.get('builds_before_reboot', 0),
                        use_mock=pf.get('use_mock'),
                        mock_target=pf.get('mock_target'),
                        mock_packages=pf.get('mock_packages'),
                        mock_copyin_files=pf.get('mock_copyin_files'),
                        enable_pymake=enable_pymake,
                        tooltool_manifest_src=pf.get('tooltool_manifest_src'),
                        tooltool_script=pf.get('tooltool_script'),
                        tooltool_url_list=config.get('tooltool_url_list', []),
                        **l10n_kwargs
                    )
                    # eg. Thunderbird comm-aurora linux l10n nightly
                    slavebuilddir = normalizeName('%s-%s-l10n-nightly' % (name, platform), pf['stage_product'], max_=50)
                    mozilla2_l10n_nightly_builder = {
                        'name': l10nNightlyBuilders[nightly_builder]['l10n_builder'],
                        'slavenames': pf.get('l10n_slaves', pf['slaves']),
                        'builddir': '%s-%s-l10n-nightly' % (name, platform),
                        'slavebuilddir': slavebuilddir,
                        'factory': mozilla2_l10n_nightly_factory,
                        'category': name,
                        'nextSlave': _nextAWSSlave_sort,
                        'properties': {'branch': name,
                                       'platform': platform,
                                       'product': pf['stage_product'],
                                       'stage_platform': stage_platform,
                                       'slavebuilddir': slavebuilddir},
                    }
                    branchObjects['builders'].append(
                        mozilla2_l10n_nightly_builder)

        # end do_nightly

        # We still want l10n_dep builds if nightlies are off
        if config['enable_l10n'] and config['enable_l10n_onchange'] and \
           platform in config['l10n_platforms']:
                dep_kwargs = {}
                if config.get('call_client_py', False):
                    dep_kwargs['callClientPy'] = True
                    dep_kwargs['clientPyConfig'] = {
                        'chatzilla_repo_path': config.get('chatzilla_repo_path', ''),
                        'cvsroot': config.get('cvsroot', ''),
                        'inspector_repo_path': config.get('inspector_repo_path', ''),
                        'moz_repo_path': config.get('moz_repo_path', ''),
                        'skip_blank_repos': config.get('skip_blank_repos', False),
                        'venkman_repo_path': config.get('venkman_repo_path', ''),
                    }
                mozconfig = os.path.join(
                    os.path.dirname(pf['src_mozconfig']), 'l10n-mozconfig')
                mozilla2_l10n_dep_factory = NightlyRepackFactory(
                    env=platform_env,
                    objdir=l10n_objdir,
                    platform=platform,
                    hgHost=config['hghost'],
                    tree=config['l10n_tree'],
                    project=pf['product_name'],
                    appName=pf['app_name'],
                    enUSBinaryURL=config['enUS_binaryURL'],
                    mozillaDir=config.get('mozilla_dir', None),
                    mozillaSrcDir=config.get('mozilla_srcdir', None),
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
                    buildSpace=l10nSpace,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                    signingServers=secrets.get(pf.get('dep_signing_servers')),
                    baseMirrorUrls=config.get('base_mirror_urls'),
                    extraConfigureArgs=config.get('l10n_extra_configure_args', []),
                    buildsBeforeReboot=pf.get('builds_before_reboot', 0),
                    use_mock=pf.get('use_mock'),
                    mock_target=pf.get('mock_target'),
                    mock_packages=pf.get('mock_packages'),
                    mock_copyin_files=pf.get('mock_copyin_files'),
                    mozconfig=mozconfig,
                    enable_pymake=enable_pymake,
                    tooltool_manifest_src=pf.get('tooltool_manifest_src'),
                    tooltool_script=pf.get('tooltool_script'),
                    tooltool_url_list=config.get('tooltool_url_list', []),
                    **dep_kwargs
                )
                # eg. Thunderbird comm-central linux l10n dep
                slavebuilddir = normalizeName('%s-%s-l10n-dep' % (name, platform), pf['stage_product'], max_=50)
                mozilla2_l10n_dep_builder = {
                    'name': l10nBuilders[pf['base_name']]['l10n_builder'],
                    'slavenames': pf.get('l10n_slaves', pf['slaves']),
                    'builddir': '%s-%s-l10n-dep' % (name, platform),
                    'slavebuilddir': slavebuilddir,
                    'factory': mozilla2_l10n_dep_factory,
                    'category': name,
                    'nextSlave': _nextAWSSlave_sort,
                    'properties': {'branch': name,
                                   'platform': platform,
                                   'stage_platform': stage_platform,
                                   'product': pf['stage_product'],
                                   'slavebuilddir': slavebuilddir},
                }
                branchObjects['builders'].append(mozilla2_l10n_dep_builder)

        if config['enable_valgrind'] and \
                platform in config['valgrind_platforms']:
            valgrind_env = pf['env'].copy()
            valgrind_env['REVISION'] = WithProperties("%(revision)s")
            mozilla2_valgrind_factory = ScriptFactory(
                scriptRepo="%s%s" % (config['hgurl'],
                                     config['build_tools_repo_path']),
                scriptName='scripts/valgrind/valgrind.sh',
                use_mock=pf.get('use_mock'),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                env=valgrind_env,
                reboot_command=['python',
                                'scripts/buildfarm/maintenance/count_and_reboot.py',
                                '-f', './reboot_count.txt',
                                '-n', '0',
                                '-z'],
                tooltool_manifest_src=pf.get('tooltool_manifest_src'),
                tooltool_script=pf.get('tooltool_script'),
                tooltool_url_list=config.get('tooltool_url_list', []),
                platform=platform,
            )
            mozilla2_valgrind_builder = {
                'name': '%s valgrind' % pf['base_name'],
                'slavenames': pf['slaves'],
                'builddir': '%s-%s-valgrind' % (name, platform),
                'slavebuilddir': normalizeName('%s-%s-valgrind' % (name, platform), pf['stage_product']),
                'factory': mozilla2_valgrind_factory,
                'category': name,
                'env': valgrind_env,
                'nextSlave': _nextAWSSlave_sort,
                'properties': {'branch': name,
                               'platform': platform,
                               'stage_platform': stage_platform,
                               'product': pf['stage_product'],
                               'slavebuilddir': normalizeName('%s-%s-valgrind' % (name, platform), pf['stage_product'])},
            }
            branchObjects['builders'].append(mozilla2_valgrind_builder)

        if platform in ('linux64',):
            if config.get('enable_blocklist_update', False) or \
               config.get('enable_hsts_update', False) or \
               config.get('enable_hpkp_update', False):
                periodicFileUpdateBuilder = generatePeriodicFileUpdateBuilder(
                    config, name, platform, pf['base_name'], pf['slaves'])
                branchObjects['builders'].append(periodicFileUpdateBuilder)

        # -- end of per-platform loop --

    # Make sure builders have the right properties
    addBuilderProperties(branchObjects['builders'])

    return branchObjects


def _makeGenerateMozharnessTalosBuilderArgs(suite, talos_branch, platform,
                                            factory_kwargs, branch_config, platform_config):
    mh_conf = platform_config['mozharness_config']

    extra_args = []
    if 'android' not in platform:
        extra_args = ['--suite', suite,
                      '--add-option',
                      ','.join(['--webServer', 'localhost']),
                      '--branch-name', talos_branch,
                      '--cfg', mh_conf['config_file']]
        if factory_kwargs['fetchSymbols']:
            extra_args += ['--download-symbols', 'ondemand']
        if factory_kwargs["talos_from_source_code"]:
            extra_args.append('--use-talos-json')
        scriptpath = "scripts/talos_script.py"
    # add branch config specification if blobber is enabled
    if branch_config.get('blob_upload'):
        extra_args.extend(['--blob-upload-branch', talos_branch])
    args = {
        'platform': platform,
        'mozharness_repo': branch_config['mozharness_repo'],
        'script_path': scriptpath,
        'hg_bin': platform_config[
            'mozharness_config']['hg_bin'],
        'mozharness_python': platform_config[
            'mozharness_config']['mozharness_python'],
        'extra_args': extra_args,
        'script_timeout': platform_config['mozharness_config'].get('script_timeout', 3600),
        'script_maxtime': (platform_config['mozharness_config'].get('talos_script_maxtime', platform_config['mozharness_config'].get('script_maxtime', 7200))),
        'reboot_command': platform_config[
            'mozharness_config'].get('reboot_command'),
        'script_repo_manifest': branch_config.get(
                'script_repo_manifest'),
        'relengapi_archiver_repo_path': branch_config.get('mozharness_archiver_repo_path'),
        'relengapi_archiver_rev': branch_config.get('mozharness_archiver_rev'),
    }
    return args

def generateTalosBranchObjects(branch, branch_config, PLATFORMS, SUITES,
                               ACTIVE_UNITTEST_PLATFORMS):
    branchObjects = {'schedulers': [], 'builders': [], 'status': [],
                     'change_source': []}
    # prettyNames is a mapping to pass to the try_parser for validation
    prettyNames = {}

    # We only understand a couple PGO strategies
    assert branch_config['pgo_strategy'] in ('per-checkin', 'periodic', 'try', None), \
        "%s is not an understood PGO strategy" % branch_config[
        'pgo_strategy']

    buildBranch = branch_config['build_branch']

    for platform, platform_config in PLATFORMS.iteritems():
        if 'platforms' in branch_config and \
           platform in branch_config['platforms'] and \
           not branch_config['platforms'][platform].get('enable_talos', True):
            continue

        # if platform is in the branch config check for overriding slave_platforms at the branch level
        # before creating the builders & schedulers
        if not branch_config['platforms'].get(platform):
            continue

        if platform_config.get('is_mobile', False):
            branchName = branch_config['mobile_branch_name']
            talosBranch = branch_config.get(
                'mobile_talos_branch', branch_config['mobile_tinderbox_tree'])
        else:
            branchName = branch_config['branch_name']
            talosBranch = branch_config['tinderbox_tree']

        branchProperty = branch

        stage_platform = platform_config.get('stage_platform', platform)
        stage_product = platform_config['stage_product']

        # Decide whether this platform should have PGO builders created
        if branch_config['pgo_strategy'] and platform in branch_config['pgo_platforms']:
            create_pgo_builders = True
        else:
            create_pgo_builders = False

        slave_platforms = branch_config['platforms'][platform].get(
            'slave_platforms', platform_config.get('slave_platforms', []))
        talos_slave_platforms = branch_config['platforms'][platform].get(
            'talos_slave_platforms', platform_config.get('talos_slave_platforms', []))

        # Mapping of skip configuration to talos builder names
        talos_builders = collections.defaultdict(list)
        talos_pgo_builders = collections.defaultdict(list)

        try_default = True
        if not branch_config['platforms'][platform].get('try_by_default', True):
            try_default = False
        elif not platform_config.get('try_by_default', True):
            try_default = False

        for slave_platform in set(slave_platforms + talos_slave_platforms):
            if slave_platform not in platform_config:
                continue
            platform_name = platform_config[slave_platform]['name']
            # this is to handle how a platform has more than one slave
            # platform
            slave_platform_try_default = try_default
            if not platform_config[slave_platform].get('try_by_default', True):
                slave_platform_try_default = False
            platformPrettyName = platform_name
            if not slave_platform_try_default:
                platformPrettyName += ' try-nondefault'
            prettyNames.setdefault(platform, []).append(platformPrettyName)
            for suite, talosConfig in SUITES.iteritems():
                tests, merge, extra, platforms = branch_config[
                    '%s_tests' % suite]

                if tests == 0 or slave_platform not in platforms or slave_platform not in talos_slave_platforms:
                    continue

                assert tests == 1

                skipconfig = None
                if (slave_platform in branch_config['platforms'][platform] and
                   'skipconfig' in branch_config['platforms'][platform][slave_platform]):
                    skipconfig = branch_config['platforms'][platform][slave_platform]['skipconfig'].get(('talos', suite))

                # We only want to append '-Non-PGO' to platforms that
                # also have PGO builds.
                if create_pgo_builders:
                    opt_branch_name = branchName + '-Non-PGO'
                    opt_talos_branch = talosBranch + '-Non-PGO'
                else:
                    opt_branch_name = branchName
                    opt_talos_branch = talosBranch

                factory_kwargs = {
                    "OS": slave_platform,
                    "supportUrlBase": branch_config['support_url_base'],
                    "envName": platform_config['env_name'],
                    "workdirBase": "../talos-data",
                    "buildBranch": buildBranch,
                    "branchName": opt_branch_name,
                    "branch": branch,
                    "talosBranch": opt_talos_branch,
                    "configOptions": talosConfig,
                    "talosCmd": branch_config['talos_command'],
                    "fetchSymbols": branch_config['fetch_symbols'] and
                    platform_config[
                        slave_platform].get('download_symbols', True),
                    "talos_from_source_code": branch_config.get('talos_from_source_code', False),
                    "credentialsFile": os.path.join(os.getcwd(), "BuildSlaves.py"),
                    "datazillaUrl": branch_config.get('datazilla_url')
                }
                factory_kwargs.update(extra)

                builddir = "%s_%s_test-%s" % (
                    branch, slave_platform, suite)
                slavebuilddir = 'test'
                properties = {
                    'branch': branchProperty,
                    'platform': slave_platform,
                    'stage_platform': stage_platform,
                    'product': stage_product,
                    'builddir': builddir,
                    'slavebuilddir': slavebuilddir,
                }

                assert branch_config.get('mozharness_talos', True) and platform_config[slave_platform].get('mozharness_talos', True)
                args = _makeGenerateMozharnessTalosBuilderArgs(suite, opt_talos_branch, platform,
                                                               factory_kwargs, branch_config, platform_config)
                factory = generateMozharnessTalosBuilder(**args)
                properties['script_repo_revision'] = branch_config['mozharness_tag']
                properties['repo_path'] = branch_config['repo_path']

                builder = {
                    'name': "%s %s talos %s" % (platform_name, branch, suite),
                    'slavenames': platform_config[slave_platform]['slaves'],
                    'builddir': builddir,
                    'slavebuilddir': slavebuilddir,
                    'factory': factory,
                    'category': branch,
                    'properties': properties,
                    'env': MozillaEnvironments[platform_config['env_name']],
                }

                if not merge:
                    nomergeBuilders.add(builder['name'])

                pgo_only_suites = set(branch_config.get('pgo_only_suites', []))

                if suite not in pgo_only_suites:
                    talos_builders[skipconfig].append(builder['name'])
                    branchObjects['builders'].append(builder)

                if create_pgo_builders:
                    properties = {
                        'branch': branchProperty,
                        'platform': slave_platform,
                        'stage_platform': stage_platform + '-pgo',
                        'product': stage_product,
                        'builddir': builddir,
                        'slavebuilddir': slavebuilddir,
                    }
                    assert branch_config.get('mozharness_talos') and not platform_config.get('is_mobile')
                    args = _makeGenerateMozharnessTalosBuilderArgs(suite, talosBranch, platform,
                                                                   factory_kwargs, branch_config, platform_config)
                    pgo_factory = generateMozharnessTalosBuilder(**args)
                    properties['script_repo_revision'] = branch_config['mozharness_tag']
                    properties['repo_path'] = branch_config['repo_path']

                    pgo_builder = {
                        'name': "%s %s pgo talos %s" % (platform_name, branch, suite),
                        'slavenames': platform_config[slave_platform]['slaves'],
                        'builddir': builddir + '-pgo',
                        'slavebuilddir': slavebuilddir,
                        'factory': pgo_factory,
                        'category': branch,
                        'properties': properties,
                        'env': MozillaEnvironments[platform_config['env_name']],
                    }

                    if not merge:
                        nomergeBuilders.add(pgo_builder['name'])
                    branchObjects['builders'].append(pgo_builder)
                    talos_pgo_builders[1].append(pgo_builder['name'])

            # Skip talos only platforms, not active platforms, branches
            # with disabled unittests
            if slave_platform not in slave_platforms:
                continue
            if platform not in ACTIVE_UNITTEST_PLATFORMS:
                continue
            if not branch_config.get('enable_unittests', True):
                continue
            if slave_platform not in branch_config['platforms'][platform]:
                continue

            testTypes = []
            # unittestSuites are gathered up for each platform from
            # config.py
            unittestSuites = []
            if branch_config['platforms'][platform].get('enable_opt_unittests'):
                testTypes.append('opt')
            if branch_config['platforms'][platform].get('enable_debug_unittests'):
                testTypes.append('debug')

            merge_tests = branch_config.get('enable_merging', True)

            for test_type in testTypes:
                test_builders = []
                pgo_builders = []
                triggeredUnittestBuilders = []
                pgoUnittestBuilders = []
                unittest_suites = "%s_unittest_suites" % test_type
                build_dir_prefix = platform_config[slave_platform].get(
                    'build_dir_prefix', slave_platform)
                if test_type == "debug":
                    # Debug tests always need to download symbols for
                    # runtime assertions
                    pf = branch_config['platforms'][platform]
                    if pf.get('download_symbols', False) or pf.get('download_symbols_ondemand', True):
                        # Copy the platform config so we can modify it here
                        # safely
                        branch_config['platforms'][platform] = deepcopy(branch_config['platforms'][platform])
                        # Get a new reference
                        pf = branch_config['platforms'][platform]
                        pf['download_symbols'] = True
                        pf['download_symbols_ondemand'] = False
                    slave_platform_name = "%s-debug" % build_dir_prefix
                elif test_type == "mobile":
                    slave_platform_name = "%s-mobile" % build_dir_prefix
                else:
                    slave_platform_name = build_dir_prefix

                # in case this builder is a set of suites
                builders_with_sets_mapping = {}
                # create builder names for schedulers
                for suites_name, suites in branch_config['platforms'][platform][slave_platform][unittest_suites]:
                    test_builders.extend(generateTestBuilderNames(
                        '%s %s %s test' % (platform_name, branch, test_type), suites_name, suites))
                    if create_pgo_builders and test_type == 'opt':
                        pgo_builders.extend(generateTestBuilderNames(
                                            '%s %s pgo test' % (platform_name, branch), suites_name, suites))
                    if isinstance(suites, dict) and 'trychooser_suites' in suites:
                        for s in suites['trychooser_suites']:
                            builders_with_sets_mapping[s] = suites_name

                scheduler_slave_platform_identifier = platform_config[slave_platform].get(
                    'scheduler_slave_platform_identifier', slave_platform)
                triggeredUnittestBuilders.append(
                    (
                        'tests-%s-%s-%s-unittest' % (
                            branch, scheduler_slave_platform_identifier, test_type),
                        test_builders, merge_tests))
                if create_pgo_builders and test_type == 'opt':
                    pgoUnittestBuilders.append(
                        (
                            'tests-%s-%s-pgo-unittest' % (
                                branch, scheduler_slave_platform_identifier),
                            pgo_builders, merge_tests))

                for suites_name, suites in branch_config['platforms'][platform][slave_platform][unittest_suites]:
                    # create the builders
                    test_builder_kwargs = {
                        "config": branch_config,
                        "branch_name": branch,
                        "platform": platform,
                        "name_prefix": "%s %s %s test" % (platform_name, branch, test_type),
                        "build_dir_prefix": "%s_%s_test" % (branch, slave_platform_name),
                        "suites_name": suites_name,
                        "suites": suites,
                        "mochitestLeakThreshold": branch_config.get('mochitest_leak_threshold', None),
                        "crashtestLeakThreshold": branch_config.get('crashtest_leak_threshold', None),
                        "slaves": platform_config[slave_platform]['slaves'],
                        "resetHwClock": branch_config['platforms'][platform][slave_platform].get('reset_hw_clock', False),
                        "stagePlatform": stage_platform,
                        "stageProduct": stage_product
                    }
                    test_builder_chunks = None
                    assert suites['use_mozharness']
                    test_builder_kwargs['mozharness_repo'] = branch_config['mozharness_repo']
                    test_builder_kwargs['mozharness_tag'] = branch_config['mozharness_tag']
                    test_builder_kwargs['mozharness'] = True
                    test_builder_kwargs['script_repo_manifest'] = branch_config.get('script_repo_manifest')
                    test_builder_kwargs['relengapi_archiver_repo_path'] = branch_config.get('mozharness_archiver_repo_path')
                    test_builder_kwargs['relengapi_archiver_rev'] = branch_config.get('mozharness_archiver_rev')
                    # allow mozharness_python to be overridden per test slave platform in case Python
                    # not installed to a consistent location.
                    if 'mozharness_config' in platform_config[slave_platform] and \
                            'mozharness_python' in platform_config[slave_platform]['mozharness_config']:
                        test_builder_kwargs['mozharness_python'] = \
                            platform_config[slave_platform]['mozharness_config']['mozharness_python']
                    else:
                        test_builder_kwargs['mozharness_python'] = platform_config['mozharness_config']['mozharness_python']
                    if suites_name in branch_config['platforms'][platform][slave_platform].get('suite_config', {}):
                        test_builder_kwargs['mozharness_suite_config'] = deepcopy(branch_config['platforms'][platform][slave_platform]['suite_config'][suites_name])
                    else:
                        test_builder_kwargs[
                            'mozharness_suite_config'] = {}
                    test_builder_kwargs['mozharness_suite_config']['hg_bin'] = platform_config['mozharness_config']['hg_bin']
                    test_builder_kwargs['mozharness_suite_config']['reboot_command'] = platform_config['mozharness_config']['reboot_command']
                    test_builder_kwargs['mozharness_suite_config']['env'] = MozillaEnvironments.get('%s-unittest' % platform, {}).copy()
                    test_builder_kwargs['mozharness_suite_config']['env'].update(branch_config['platforms'][platform].get('unittest-env', {}))
                    if branch_config.get('blob_upload') and suites.get('blob_upload'):
                        test_builder_kwargs['mozharness_suite_config']['blob_upload'] = True
                    if suites.get('download_symbols', True) and branch_config['fetch_symbols'] and \
                            branch_config['platforms'][platform][slave_platform].get('download_symbols', True):
                        if test_type == 'opt':
                            test_builder_kwargs['mozharness_suite_config']['download_symbols'] = 'ondemand'
                        else:
                            test_builder_kwargs['mozharness_suite_config']['download_symbols'] = 'true'
                    if test_type == 'opt':
                        test_builder_kwargs['is_debug'] = False
                    else:
                        test_builder_kwargs['is_debug'] = True
                    if suites.get('totalChunks'):
                        test_builder_chunks = suites['totalChunks']

                    branchObjects['builders'].extend(
                        generateChunkedUnittestBuilders(
                            test_builder_chunks,
                            platform_name,
                            branch,
                            test_type,
                            create_pgo_builders,
                            **test_builder_kwargs
                        )
                    )

                for scheduler_name, test_builders, merge in triggeredUnittestBuilders:
                    for test in test_builders:
                        unittestSuites.append(test.split(' ')[-1])
                    scheduler_branch = ('%s-%s-%s-unittest' %
                                        (branch, platform, test_type))
                    if not merge:
                        nomergeBuilders.update(test_builders)
                    extra_args = {}
                    if branch_config.get('enable_try'):
                        scheduler_class = BuilderChooserScheduler
                        extra_args['chooserFunc'] = tryChooser
                        extra_args['prettyNames'] = prettyNames
                        extra_args['unittestSuites'] = unittestSuites
                        extra_args['buildersWithSetsMap'] = builders_with_sets_mapping
                        extra_args['buildbotBranch'] = branch
                        branchObjects['schedulers'].append(scheduler_class(
                                name=scheduler_name,
                                branch=scheduler_branch,
                                builderNames=test_builders,
                                treeStableTimer=None,
                                **extra_args
                        ))
                    else:
                        scheduler_class = Scheduler
                        suites_by_skipconfig = collections.defaultdict(list)
                        skipcount = 0
                        skiptimeout = 0
                        for test in test_builders:
                            skipcount = 0
                            skiptimeout = 0
                            if branch_config['platforms'][platform][slave_platform].get('skipconfig'):
                                #extract last word in the test string as it should correspond to the name of the test
                                test_name = test.split()[-1]
                                if (test_type, test_name) in branch_config['platforms'][platform][slave_platform]['skipconfig']:
                                    skipcount, skiptimeout = branch_config['platforms'][platform][slave_platform]['skipconfig'][test_type, test_name]
                                    builderMergeLimits[test] = skipcount
                            suites_by_skipconfig[skipcount, skiptimeout].append(test)

                        # Create a new Scheduler for every skip config
                        for (skipcount, skiptimeout), test_builders in suites_by_skipconfig.iteritems():
                            scheduler_class = Scheduler
                            s_name = scheduler_name
                            extra_args = {}

                            if skipcount > 0:
                                scheduler_class = EveryNthScheduler
                                extra_args['n'] = skipcount
                                extra_args['idleTimeout'] = skiptimeout
                                s_name = scheduler_name + "-" + str(skipcount) + "-"  + str(skiptimeout)

                            branchObjects['schedulers'].append(scheduler_class(
                                name=s_name,
                                branch=scheduler_branch,
                                builderNames=test_builders,
                                treeStableTimer=None,
                                **extra_args
                            ))
                for scheduler_name, test_builders, merge in pgoUnittestBuilders:
                    for test in test_builders:
                        unittestSuites.append(test.split(' ')[-1])
                    scheduler_branch = '%s-%s-pgo-unittest' % (
                        branch, platform)
                    if not merge:
                        nomergeBuilders.update(pgo_builders)
                    extra_args = {}
                    if branch_config.get('enable_try'):
                        scheduler_class = BuilderChooserScheduler
                        extra_args['chooserFunc'] = tryChooser
                        extra_args['prettyNames'] = prettyNames
                        extra_args['unittestSuites'] = unittestSuites
                        extra_args['buildbotBranch'] = branch
                    else:
                        scheduler_class = Scheduler
                    branchObjects['schedulers'].append(scheduler_class(
                        name=scheduler_name,
                        branch=scheduler_branch,
                        builderNames=pgo_builders,
                        treeStableTimer=None,
                        **extra_args
                    ))

        def makeTalosScheduler(builders, pgo=False):
            schedulers = []
            for skipconfig, builder_names in builders.iteritems():
                extra_args = {}
                scheduler_class = Scheduler
                skipcount = 0
                skiptimeout = 0

                if pgo:
                    name = 'tests-%s-%s-pgo-talos' % (branch, platform)
                    scheduler_branch = '%s-%s-pgo-talos' % (branch, platform)
                else:
                    name = 'tests-%s-%s-talos' % (branch, platform)
                    scheduler_branch = '%s-%s-talos' % (branch, platform)

                if branch_config.get('enable_try'):
                    scheduler_class = BuilderChooserScheduler
                    extra_args['chooserFunc'] = tryChooser
                    extra_args['prettyNames'] = prettyNames
                    extra_args['talosSuites'] = SUITES.keys()
                    extra_args['buildbotBranch'] = branch
                elif isinstance(skipconfig, tuple):
                    skipcount, skiptimeout = skipconfig
                    scheduler_class = EveryNthScheduler
                    extra_args['n'] = skipcount
                    extra_args['idleTimeout'] = skiptimeout
                    name += "-%s-%s" % (skipcount, skiptimeout)
                    for b in builder_names:
                        builderMergeLimits[b] = skipcount

                s = scheduler_class(
                    name=name,
                    branch=scheduler_branch,
                    treeStableTimer=None,
                    builderNames=builder_names,
                    **extra_args
                )
                schedulers.append(s)
            return schedulers

        # Create talos schedulers
        branchObjects['schedulers'].extend(makeTalosScheduler(talos_builders, False))
        branchObjects['schedulers'].extend(makeTalosScheduler(talos_pgo_builders, True))

    # Make sure builders have the right properties
    addBuilderProperties(branchObjects['builders'])

    return branchObjects


def mirrorAndBundleArgs(config):
    args = []
    mirrors = None
    if config.get('base_mirror_urls'):
        mirrors = ["%s/%s" % (url, config['repo_path'])
                   for url in config['base_mirror_urls']]
    if mirrors:
        for mirror in mirrors:
            args.extend(["--mirror", mirror])
    bundles = None
    if config['base_bundle_urls']:
        bundles = ["%s/%s.hg" % (url, config['repo_path'].rstrip(
            '/').split('/')[-1]) for url in config['base_bundle_urls']]
    if bundles:
        for bundle in bundles:
            args.extend(["--bundle", bundle])
    return args


def generatePeriodicFileUpdateBuilder(config, branch_name, platform, base_name, slaves):
    pf = config['platforms'].get(platform, {})
    extra_args = ['-b', config['repo_path']]

    env = pf['env'].copy()
    kwargs = {}
    kwargs['env'] = env

    extra_args += mirrorAndBundleArgs(config)
    if pf['product_name'] is not None:
        extra_args.extend(['-p', pf['product_name']])
    if config['hg_username'] is not None:
        extra_args.extend(['-u', config['hg_username']])
    if config['hg_ssh_key'] is not None:
        extra_args.extend(['-k', config['hg_ssh_key']])
    if config['file_update_on_closed_tree'] is True:
        extra_args.extend(['-c'])
    if config['file_update_set_approval'] is True:
        extra_args.extend(['-a'])
    if config['enable_blocklist_update'] is True:
        extra_args.extend(['--blocklist'])
    if config['enable_hsts_update'] is True:
        extra_args.extend(['--hsts'])
    if config['enable_hpkp_update'] is True:
        extra_args.extend(['--hpkp'])

    periodic_file_update_factory = ScriptFactory(
        "%s%s" % (config['hgurl'],
                  config['build_tools_repo_path']),
        'scripts/periodic_file_updates/periodic_file_updates.sh',
        interpreter='bash',
        extra_args=extra_args,
        **kwargs
    )
    periodic_file_update_builder = {
        'name': '%s periodic file update' % base_name,
        'slavenames': slaves,
        'builddir': '%s-%s-periodicupdate' % (branch_name, platform),
        'slavebuilddir': normalizeName('%s-%s-periodicupdate' % (branch_name, platform)),
        'factory': periodic_file_update_factory,
        'category': branch_name,
        'properties': {'branch': branch_name, 'platform': platform, 'slavebuilddir': normalizeName('%s-%s-periodicupdate' % (branch_name, platform)), 'product': 'firefox'},
    }
    return periodic_file_update_builder


def generateFuzzingObjects(config, SLAVES):
    builders = []
    f = ScriptFactory(
        config['scripts_repo'],
        'scripts/fuzzing/fuzzer.sh',
        interpreter='bash',
        script_timeout=1800,
        script_maxtime=2100,
        reboot_command=['python',
                        'scripts/buildfarm/maintenance/count_and_reboot.py',
                        '-f', './reboot_count.txt',
                        '-n', '0',
                        '-z'],
    )
    for platform in config['platforms']:
        env = MozillaEnvironments.get("%s-unittest" % platform, {}).copy()
        env['GIT_LITHIUM_REPO'] = config['lithium_repo']
        env['GIT_FUNFUZZ_REPO'] = config['funfuzz_repo']
        env['GIT_FUNFUZZ_PRIVATE_REPO'] = config['funfuzz_private_repo']
        env['GIT_FUZZMANAGER_REPO'] = config['fuzzmanager_repo']
        env['FUZZ_REMOTE_HOST'] = config['fuzzing_remote_host']
        env['FUZZ_BASE_DIR'] = config['fuzzing_base_dir']
        if 'win' in platform:
            env['PATH'] = "${MOZILLABUILD}buildbotve\\scripts;${PATH}"
        builder = {'name': 'fuzzer-%s' % platform,
                   'builddir': 'fuzzer-%s' % platform,
                   'slavenames': SLAVES[platform],
                   'nextSlave': _nextIdleSlave(config['idle_slaves']),
                   'factory': f,
                   'category': 'idle',
                   'env': env,
                   'properties': {
                       'branch': 'idle',
                       'platform': platform,
                       'product': 'fuzzing',
                   },
                   }
        builders.append(builder)
        nomergeBuilders.add(builder['name'])
    fuzzing_scheduler = PersistentScheduler(
        name="fuzzer",
        builderNames=[b['name'] for b in builders],
        numPending=2,
        pollInterval=300,  # Check every 5 minutes
    )
    return {
        'builders': builders,
        'schedulers': [fuzzing_scheduler],
    }


def generateSpiderMonkeyObjects(project, config, SLAVES):
    builders = []
    branch = config['branch']
    bconfig = config['branchconfig']

    PRETTY_NAME = '%(base_name)s %(project)s-%(variant)s build'
    NAME = PRETTY_NAME

    prettyNames = {}   # Map(variant => Map(platform => prettyName))
    builderNames = {}  # Map(variant => builder names)
    for platform, variants in config['variants'].iteritems():
        if platform not in bconfig['platforms']:
            continue

        interpreter = None
        if 'win' in platform:
            interpreter = 'bash'

        pf = bconfig['platforms'][platform]
        env = pf['env'].copy()
        env['HG_REPO'] = config['hgurl'] + bconfig['repo_path']

        for variant in variants:
            factory_platform_args = ['use_mock',
                                     'mock_target',
                                     'mock_packages',
                                     'mock_copyin_files']
            factory_kwargs = {}
            for a in factory_platform_args:
                if a in pf:
                    factory_kwargs[a] = pf[a]
            factory_kwargs['env'] = env

            extra_args = ['-r', WithProperties("%(revision)s")]
            extra_args += ['--platform', platform]  # distinguish win64
            extra_args += mirrorAndBundleArgs(bconfig)
            extra_args += [variant]
            extra_args += ['--ttserver',
                           'https://api.pub.build.mozilla.org/tooltool/']

            f = ScriptFactory(
                config['scripts_repo'],
                'scripts/spidermonkey_builds/spidermonkey.sh',
                interpreter=interpreter,
                log_eval_func=rc_eval_func({1: WARNINGS}),
                extra_args=tuple(extra_args),
                script_timeout=3600,
                reboot_command=[
                    'python',
                    'scripts/buildfarm/maintenance/count_and_reboot.py',
                    '-f', './reboot_count.txt',
                    '-n', '0',
                    '-z'],
                **factory_kwargs
            )

            name_info = {'base_name': pf['base_name'] % config,
                         'project': config['project_name'],
                         'variant': variant,
                         'branch': branch}
            name = NAME % name_info

            prettyName = PRETTY_NAME % name_info
            # try_by_default is a dict from variant name to either True
            # (meaning all platforms for which that variant is defined) or a
            # set of platform names for which the try build should be on by
            # default.
            if 'try_by_default' in config:
                if variant not in config['try_by_default']:
                    prettyName += ' try-nondefault'
                else:
                    defaults = config['try_by_default'][variant]
                    if isinstance(defaults, set) and platform not in defaults:
                        prettyName += ' try-nondefault'
            prettyNames.setdefault(variant, {})[platform] = prettyName
            builderNames.setdefault(variant, []).append(name)

            builder = {'name': name,
                       'builddir': '%s_%s_spidermonkey-%s' % (branch, platform, variant),
                       'slavebuilddir': normalizeName('%s_%s_spidermonkey-%s' % (branch, platform, variant)),
                       'slavenames': pf['slaves'],
                       'nextSlave': _nextIdleSlave(config['idle_slaves']),
                       'factory': f,
                       'category': branch,
                       'env': env,
                       'properties': {'branch': branch, 'platform': platform, 'product': 'spidermonkey'},
                       }
            builders.append(builder)
            if not bconfig.get('enable_merging', True):
                nomergeBuilders.add(name)

    def isImportant(change):
        if not isHgPollerTriggered(change, bconfig['hgurl']):
            return False

        if not shouldBuild(change):
            return False

        for f in change.files:
            if f.startswith("js/src") or f.startswith("js/public"):
                return True

        return False

    # Set up schedulers
    extra_args = {}
    schedulers = []
    if config.get("enable_schedulers", True):
        scheduler_class = None
        if config.get('enable_try'):
            scheduler_class = makePropertiesScheduler(
                BuilderChooserScheduler, [buildUIDSchedFunc])
            extra_args['chooserFunc'] = tryChooser
            extra_args['buildbotBranch'] = branch
        else:
            scheduler_class = Scheduler

        for variant in prettyNames:
            if config.get('enable_try'):
                extra_args['prettyNames'] = prettyNames[variant]
            schedulers.append(scheduler_class(
                name=project + "-" + variant,
                treeStableTimer=None,
                builderNames=builderNames[variant],
                fileIsImportant=isImportant,
                change_filter=ChangeFilter(branch=bconfig['repo_path'], filter_fn=isImportant),
                **extra_args
            ))

    return {
        'builders': builders,
        'schedulers': schedulers,
    }

def generateProjectObjects(project, config, SLAVES, all_builders=None):
    builders = []
    schedulers = []
    change_sources = []
    status = []
    buildObjects = {
        'builders': builders,
        'schedulers': schedulers,
        'status': status,
        'change_source': change_sources,
    }

    # Fuzzing
    if project.startswith('fuzzing'):
        fuzzingObjects = generateFuzzingObjects(config, SLAVES)
        buildObjects = mergeBuildObjects(buildObjects, fuzzingObjects)

    # Spidermonkey
    elif project.startswith('spidermonkey'):
        spiderMonkeyObjects = generateSpiderMonkeyObjects(
            project, config, SLAVES)
        buildObjects = mergeBuildObjects(buildObjects, spiderMonkeyObjects)

    return buildObjects


def makeLogUploadCommand(branch_name, config, is_try=False, is_shadow=False,
                         platform_prop="platform", product_prop=None, product=None):
    extra_args = []
    if config.get('enable_mail_notifier'):
        if config.get('notify_real_author'):
            extraRecipients = []
            sendToAuthor = True
        else:
            extraRecipients = config['email_override']
            sendToAuthor = False

        upload_cmd = 'try_mailer.py'
        extra_args.extend(['-f', 'tryserver@build.mozilla.org'])
        for r in extraRecipients:
            extra_args.extend(['-t', r])
        if sendToAuthor:
            extra_args.append("--to-author")
    else:
        upload_cmd = 'log_uploader.py'

    logUploadCmd = [sys.executable,
                    '%s/bin/%s' % (buildbotcustom.__path__[0], upload_cmd),
                    config['stage_server'],
                    '-u', config['stage_username'],
                    '-i', os.path.expanduser(
                        "~/.ssh/%s" % config['stage_ssh_key']),
                    '-b', branch_name,
                    ]

    if platform_prop:
        logUploadCmd += ['-p', WithProperties("%%(%s)s" % platform_prop)]
    logUploadCmd += extra_args

    if product_prop:
        logUploadCmd += ['--product', WithProperties("%%(%s)s" % product_prop)]
        assert not product, 'dont specify static value when using property'
    elif product:
        logUploadCmd.extend(['--product', product])

    if is_try:
        logUploadCmd.append('--try')

    if is_shadow:
        logUploadCmd.append('--shadow')

    return logUploadCmd


def _is_l10n_enabled(config):
    """returns True if mozharness desktop repacks are enabled"""
    # l10n with mozharness enabled per branch?
    try:
        return config['desktop_mozharness_repacks_enabled']
    except KeyError:
        return False


def _is_l10n_capable(config, platform):
    """returns True if the platform can do desktop repacks with mozharness"""
    # l10n with mozharness enabled per project?
    # platform config
    pf = config['platforms'][platform]
    try:
        return pf['mozharness_desktop_l10n']['capable']
    except KeyError:
        return False


def is_l10n_with_mh(config, platform):
    """mozharness desktop repacks are enabled if they are active on the project
       and 'platform' is capable of creating repacks.
    """
    return _is_l10n_enabled(config) and _is_l10n_capable(config, platform)


def mh_l10n_builders(config, platform, branch, secrets, is_nightly):
    """ returns a dictionary with builders and schedulers
        """
    # let's check if we need to create builders for this config/platform
    builders = []
    pf = config['platforms'][platform]
    name = pf['base_name']
    platform_env = pf['env'].copy()
    builder_env = platform_env.copy()
    stage_platform = pf.get('stage_platform', platform)
    # reboot command and python interpreter are defined per platform
    mozharness_python = pf.get('mozharness_python')
    reboot_command = pf['reboot_command']
    scriptRepo = '%s%s' % (config['hgurl'],
                           config['mozharness_repo_path'])
    # repacks specific configuration is in:
    # platform > mozharness_desktop_l10n
    repacks = pf['mozharness_desktop_l10n']
    product_name = pf['product_name'].capitalize()
    scriptName = repacks['scriptName']
    l10n_chunks = repacks['l10n_chunks']
    script_timeout = repacks['script_timeout']
    script_maxtime = repacks['script_maxtime']
    use_credentials_file = repacks['use_credentials_file']
    config_dir = 'single_locale'
    branch_config = os.path.join(config_dir, '%s.py' % branch)
    platform_config = os.path.join(config_dir, '%s.py' % platform)
    environment_config = os.path.join(config_dir, 'production.py')
    balrog_config = os.path.join('balrog', 'production.py')
    if config.get('staging', False):
        environment_config = os.path.join(config_dir, 'staging.py')
        balrog_config = os.path.join('balrog', 'staging.py')
    # desktop repacks run in chunks...
    builder_names = mh_l10n_builder_names(config, platform, branch, is_nightly)
    this_chunk = 0
    for bn in builder_names:
        this_chunk += 1
        builderName = bn
        builddir = mh_l10n_builddir_from_builder_name(bn, product_name)
        extra_args = ['--environment-config', environment_config,
                      '--branch-config', branch_config,
                      '--platform-config', platform_config,
                      '--total-chunks', str(l10n_chunks),
                      '--this-chunk', str(this_chunk)]
        if config.get('updates_enabled', False):
            extra_args.extend(['--balrog-config', balrog_config])
        signing_servers = secrets.get(pf.get('nightly_signing_servers'))
        factory = SigningScriptFactory(
            signingServers=signing_servers,
            scriptRepo=scriptRepo,
            scriptName=scriptName,
            script_timeout=script_timeout,
            script_maxtime=script_maxtime,
            use_credentials_file=use_credentials_file,
            interpreter=mozharness_python,
            extra_args=extra_args,
            reboot_command=reboot_command,
            relengapi_archiver_repo_path=config.get('mozharness_archiver_repo_path'),
            relengapi_archiver_rev=config.get('mozharness_archiver_rev')
        )
        slavebuilddir = normalizeName(builddir)
        builders.append({
            'name': builderName,
            'slavenames': pf.get('slaves'),
            'builddir': builddir,
            'slavebuilddir': slavebuilddir,
            'factory': factory,
            'category': name,
            'nextSlave': _nextAWSSlave_sort,
            'properties': {'branch': branch,
                           'builddir': builddir,
                           'stage_platform': stage_platform,
                           'product': pf['stage_product'],
                           'platform': platform,
                           'slavebuilddir': slavebuilddir,
                           'script_repo_revision': config['mozharness_tag'],
                           'repo_path': config['repo_path'],
                           },
            'env': builder_env
        })

    return builders


def mh_l10n_builddir_from_builder_name(builder_name, product_name):
    """transforms a builder name into a builddir"""
    # builder name is Firefox ash linux nightly l10n 1/3
    # we need: ash-lx-ntly-l10n-1_3-000000000
    # replace spaces with -
    b_dir = builder_name.replace(' ', '-')
    # remove product name
    b_dir = b_dir.replace('%s-' % product_name, '')
    # replaces / with _
    return b_dir.replace('/', '_')


def mh_l10n_scheduler_name(config, platform):
    pf = config['platforms'][platform]
    return '%s nightly l10n' % (pf['base_name'])


def mh_l10n_builder_names(config, platform, branch, is_nightly):
    # let's check if we need to create builders for this config/platform
    pf = config['platforms'][platform]
    product_name = pf['product_name']
    name = '%s %s %s l10n' % (product_name, branch, platform)
    name = name.capitalize()
    if is_nightly:
        name = '%s nightly' % (name)
    repacks = pf['mozharness_desktop_l10n']

    l10n_chunks = repacks['l10n_chunks']
    if l10n_chunks == 1:
        names = [name]
    else:
        names = ["%s-%s" % (name, chunk) for chunk in range(1, l10n_chunks + 1)]
    return names


def addBuilderProperties(builders):
    for b in builders:
        if not isinstance(b['factory'], ScriptFactory):
            continue

        # TODO: do the same for 'master' property?
        if 'basedir' in b['properties']:
            continue

        if 'slavebuilddir' in b:
            slavebuilddir = b['slavebuilddir']
        else:
            slavebuilddir = b['builddir']

        platform = b['properties']['platform']

        if platform.startswith('win') or platform.startswith('xp-'):
            # On Windows, test slaves use C:\slave\test, but build slaves
            # use /c/builds/moz2_slave
            if slavebuilddir == 'test':  # TODO: This check is too fragile
                rootdir = r'C:\slave'
                basedir = '%s\%s' % (rootdir, slavebuilddir)
            else:
                rootdir = '/c/builds/moz2_slave'
                basedir = '%s/%s' % (rootdir, slavebuilddir)
        else:
            rootdir = '/builds/slave'
            basedir = '%s/%s' % (rootdir, slavebuilddir)

        b['properties']['basedir'] = basedir
