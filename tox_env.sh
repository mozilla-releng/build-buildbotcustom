#!/bin/bash
TOX_DIR="${1}"
echo "dir: ${TOX_DIR}"

function hgme {
    repo="${1}"
    if [ ! -d "${TOX_DIR}/${repo}" ]; then
        hg clone https://hg.mozilla.org/build/${repo} "${TOX_DIR}/${repo}"
    else
        hg status -un0 -R "${TOX_DIR}/${repo}" | xargs rm -rf
        hg pull -u -R "${TOX_DIR}/${repo}"
    fi
}

hgme tools
hgme buildbot
