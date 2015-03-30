#!/bin/bash -e
[ -z "${1}" ] && exit 1
TOX_DIR="${1}"

function hgme {
    repo="${1}"
    if [ ! -d "${TOX_DIR}/${repo}" ]; then
        hg clone https://hg.mozilla.org/build/${repo} "${TOX_DIR}/${repo}"
    else
        # this is equivalent to hg purge but doesn't require the hg purge plugin to be enabled
        hg status -un0 -R "${TOX_DIR}/${repo}" | xargs --no-run-if-empty --null rm -rf
        hg pull -u -R "${TOX_DIR}/${repo}"
    fi
}

hgme tools
hgme buildbot

# top level dir has a __init__.py so the package name is the same as the directory
# name the repo is checked out in - so if this is not buildbotcustom (e.g.
# build-buildbotcustom) then the package name will be wrong. so we create a
# symbolic link to the top-level-dir from inside .tox directory, to guarantee the
# name is buildbotcustom, and add this to the PYTHONPATH in tox.ini file.
[ ! -L "${TOX_DIR}/buildbotcustom" ] && ln -s "${TOX_DIR}/.." "${TOX_DIR}/buildbotcustom" || :
