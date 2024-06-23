import logging
import sys
from datetime import datetime
from tqdm.auto import tqdm as tqdm_orig
import numpy as np
import os

from .model import File, Package

logger = logging.getLogger(__name__)

PBAR_OFFSET = 8
PBAR_FORMAT = (" " * PBAR_OFFSET) + ">>>>>>>  {l_bar}{bar}{r_bar}"


class tqdm(tqdm_orig):
    def __init__(self, *args, **kwargs):
        kwargs["bar_format"] = PBAR_FORMAT
        super().__init__(*args, **kwargs)


def run(cmd: str, chroot_dir: str | None = None):
    import os
    import subprocess

    args = ["bash", "-c", cmd]
    if chroot_dir:
        args = ["chroot", chroot_dir, *args]
    if os.geteuid() != 0:
        args = ["sudo", *args]

    return subprocess.run(args, stdout=subprocess.PIPE).stdout.decode("utf-8")


def run_nested(cmd: str, dir: str):
    return run(cmd, chroot_dir=dir)


def get_files(dir: str):
    if os.getuid() == 0:
        # Read the dir directly to enable progress bar
        # and skip IPC and to string conversion
        pbar = tqdm(total=300_000, desc="Reading files")
        all_files = []
        for root, _, files in os.walk(dir):
            if "/sysroot/ostree" in root:
                continue
            if "/.build-id/" in root:
                continue

            for file in files:
                fn = os.path.join(root, file)
                if os.path.islink(fn):
                    s = 0
                else:
                    s = os.path.getsize(fn)

                # remove leading dot
                all_files.append(File(fn[1:], s))
                pbar.update(1)
        pbar.close()
    else:
        all_files = []

        for line in run(f"'{sys.executable}' -m rechunk.walker '{dir}'").splitlines():
            idx = line.index(" ")
            size = int(line[:idx])
            name = line[idx + 1 :]
            all_files.append(File(name, size))

    return all_files


def get_update_matrix(packages: list[Package], biweekly: bool = True):
    # Update matrix for packages
    # For each package, it lists the times it was updated last year
    # The frequency is bi-weekly, assuming that a distro might update 2x
    # per week.
    if biweekly:
        n_segments = 106
    else:
        n_segments = 53
    p_upd = np.zeros((len(packages), n_segments), dtype=np.bool)

    pkg_nochangelog = []
    curr = datetime.now()
    for i, p in enumerate(packages):
        for u in p.updates:
            if (curr - u).days > 365:
                continue

            _, w, d = u.isocalendar()
            if biweekly:
                p_upd[i, 2 * w + (d >= 4)] = 1
            else:
                p_upd[i, w] = 1

        # Some packages have no changelog, assume they always update
        # Use updates from all previous years from this. Some packages
        # may have not updated last year.
        if len(p.updates) <= 2:
            p_upd[i] = 1
            pkg_nochangelog.append(p.name)

    logger.info(
        f"Found {len(pkg_nochangelog)} packages with no changelog:\n{str(pkg_nochangelog)}"
    )

    return p_upd
