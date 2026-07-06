# Copyright 2026 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Setup a Bazel workspace for testing ROS distributions in CI.
"""

import argparse
import gzip
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Set


def scan_module_for_dependencies(
    module_dot_bazel: Path,
    modules_path: Path,
    include_bcr: bool = False,
    include_rcr: bool = True,
) -> Dict[str, str]:
    """
    Returns a list of package names and their versions.
    """
    packages = {}
    with open(module_dot_bazel, "r") as f:
        content = f.read()
        matches = re.findall(
            r'bazel_dep\(\s*name\s*=\s*"([^"]+)"\s*,\s*version\s*=\s*"([^"]+)"',
            content,
        )
        for name, version in matches:
            is_rcr_module = (modules_path / name).exists()
            if (include_rcr and is_rcr_module) or (
                include_bcr and not is_rcr_module
            ):
                packages[name] = version
    return packages


VARIANTS = {
    "core": "ros_core",
    "base": "ros_base",
    "desktop": "desktop",
    "desktop_full": "desktop_full",
    "perception": "perception",
    "simulation": "simulation",
}

APT_SLICES = {
    "nongui": re.compile(r"."),
    "navigation": re.compile(
        r"(^|-)nav2?($|-)|navigation|slam|map-server|octomap-server|amcl|planner|costmap"
    ),
}

GUI_PACKAGE_RE = re.compile(
    r"(^|-)(rviz2?|rqt|qt[56]?|qml[0-9]*|gui|libgui|gazebo|gz|ignition|webots|viz|visualization(?!-msgs(?:$|-))|renderer|rendering|render|ogre|glu|libopengl|opengl|turtlebot[0-9]?|turtlesim|tb3|tb4|demos?|viewer|editor|view|webview|foxglove)($|-)",
    re.IGNORECASE,
)

INTERFACE_PACKAGE_PARTS = {"action", "idl", "msg", "srv"}
SKIPPED_OVERLAY_PACKAGE_PARTS = {"test", "tests"}
GENERATED_ROOT_TARGETS = ("c", "cc", "idl", "proto", "py", "rs")

BAZELRC_TEMPLATE = """# Copyright 2026 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

## COMMON OPTIONS

# This suppresses warnings that bazel emits when two dependencies rely
# on different versions of a single module. This is what allows us to
# load 0.0.0 versions of RCR package and have bazel resolve the correct
# package for the ROS distribution we are using.
common --check_direct_dependencies=off

# This tells Bazel to look locally for RCR modules at the root of this
# project, additional "staged" BCR modules in the bcr_staging folder
# in this workspace, and ultimately at the BCR for the rest.
common --registry=file://%workspace%/..             \\
       --registry=file://%workspace%/../bcr_staging \\
       --registry=https://bcr.bazel.build

# This provides read-only access to a shared Bazel remote cache offered
# by Intrinsic. It helps speed up fresh checkouts from upstream.
common --remote_cache=https://storage.googleapis.com/intrinsic-opensource-buildcache
common --remote_upload_local_results=false
common --remote_cache_compression=true

# Critical ROS environment variables needed at test-time.
common --test_env=ROS_DISTRO="{distro}"
common --test_env=ROS_HOME=".ros"
common --test_env=RMW_IMPLEMENTATION="rmw_fastrtps_cpp"
common --test_env=LD_LIBRARY_PATH=lib

# Critical ROS environment variables needed at run-time.
common --run_env=ROS_DISTRO="{distro}"
common --run_env=ROS_HOME=".ros"
common --run_env=RMW_IMPLEMENTATION="rmw_fastrtps_cpp"
common --run_env=LD_LIBRARY_PATH=lib

# Propagate select X11 variables through to the test sandbox, so that
# any test that uses the display has access to it.
common --test_env=DISPLAY
common --test_env=XAUTHORITY
common --test_env=WAYLAND_DISPLAY

# Our approach to message generation works on a per-message dependency
# chain. We need to be able to merge messages into 
common --incompatible_default_to_explicit_init_py

# This restricts the environment variables available to build actions, 
# keeping the build environment more hermetic.
common --incompatible_strict_action_env

# Use a shared cache for bazel builds, which speeds up CI builds.
common --remote_cache=https://storage.googleapis.com/intrinsic-opensource-buildcache
common --remote_cache_compression=true
common --remote_upload_local_results=false

# CI specific options for remote caching and credentials.
common:ci --remote_upload_local_results=true
common:ci --google_default_credentials

# We support the following variants of ROS distributions. This allows you
# to build or test the entire variant with a --config flag.
{variants}

# Automatically pick configurations for the platform running bazel.
# For example, macos on macos, linux on linux, etc.
common --enable_platform_specific_config

## BUILD OPTIONS

# Ensure that we use toolchains_llvm instead of the host toolchain.
build --repo_env="BAZEL_DO_NOT_DETECT_CPP_TOOLCHAIN=1"

# Our bazel distribution provides conversion utilities for transforming
# ROS messages to .proto files. This prevent protobuf from trying to
# recompile itself in response to environment changes.
build --@protobuf//bazel/toolchains:prefer_prebuilt_protoc

# This tells our hermetic LLVM compiler to stub some GCC functions that
# are used in several places around the ROS Bazel workspace/
build --@llvm//config:experimental_stub_libgcc_s=True

# ld64's two-level namespace requires every undefined symbol in a .dylib to
# be resolvable among the libraries passed directly to its link command.
# Our rosidl-generated cc_shared_library targets commonly need symbols from
# libraries that are several dynamic_deps hops away, which the ELF loader on
# Linux resolves transitively at process load time with no issue. On Darwin
# we fall back to flat-namespace symbol lookup at load time instead.
build:macos --linkopt=-Wl,-undefined,dynamic_lookup
build:macos --host_linkopt=-Wl,-undefined,dynamic_lookup

# The Apple SDK framework directories contain module.modulemap files that
# activate Clang's implicit module system once a Frameworks directory is in
# scope (as it now implicitly is, via --sysroot). Libraries like abseil-cpp
# include CoreFoundation headers without declaring a module dependency, which
# the module system rejects. -fno-implicit-module-maps is a clang driver flag
# (not a cc1 flag); pass it directly without -Xclang.
build:macos --copt=-fno-implicit-module-maps
build:macos --host_copt=-fno-implicit-module-maps

# Apple SDK framework headers use the CF_ENUM() macro, which expands to a forward
# enum declaration inside a typedef -- a Clang extension that Clang supports but
# warns about starting with Clang 22 (-Welaborated-enum-base). Suppress it so
# CoreFoundation/CFBase.h and friends compile cleanly with the hermetic toolchain.
build:macos --copt=-Wno-elaborated-enum-base
build:macos --host_copt=-Wno-elaborated-enum-base

# handle unresolved symlinks in frameworks
build:macos --experimental_allow_unresolved_symlinks
build:macos --nested_set_depth_limit=5000


## TEST OPTIONS

# This restricts our test sandboxes from accessing the network, which is
# important if you want to prevent destructive interference on the ROS
# messaging system between tests running in parallel. On Linux, this still
# permits loopback, so DDS discovery over loopback keeps working. macOS's has
# profile only carves out plain "localhost:*" traffic, not UDP
# multicast groups, which is what FastRTPS's discovery protocol actually
# uses -- so multi-node/multi-context tests (e.g. rcl_action's graph tests)
# hang until they hit their own timeout under the sandbox. For the tests that
# need real multicast discovery, we use "no-sandbox" + "exclusive" tags on
# just the handful of tests that need real multicast discovery.
test --sandbox_default_allow_network=false

# CI specific quirks
test:ci --flaky_test_attempts=5
test:ci --local_test_jobs=1
"""

def get_copyright_header() -> str:
    return """# Copyright 2026 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""

def calculate_packages_for_variant(
    module_dir: Path, start_name: str, start_version: str
) -> List[str]:
    """
    Performs a BFS traversal starting from the given module name and version to collect
    all transitive dependencies.
    """
    visited = set()
    queue = [(start_name, module_dir / start_name / start_version / 'MODULE.bazel')]
    while queue:
        current_name, current_file = queue.pop(0)
        if current_name in visited:
            continue
        visited.add(current_name)
        if not current_file.exists():
            continue
        with open(current_file, "r") as f:
            content = f.read()
            deps = re.findall(
                r'bazel_dep\(name\s*=\s*"([^"]+)"\s*,\s*version\s*=\s*"([^"]+)"\)',
                content,
            )
            for next_name, next_version in deps:
                next_file = module_dir / next_name / next_version / 'MODULE.bazel'
                if next_file.exists():
                    if next_name not in visited:
                        queue.append((next_name, next_file))
    visited.discard("rosdistro")
    return sorted(list(visited))


def _parse_apt_packages(data: bytes, ros_distro: str) -> Dict[str, List[str]]:
    packages: Dict[str, List[str]] = {}
    prefix = f"ros-{ros_distro}-"
    text = gzip.decompress(data).decode("utf-8", errors="replace")
    for stanza in text.split("\n\n"):
        package_match = re.search(r"^Package: ([^\n]+)", stanza, re.MULTILINE)
        if not package_match:
            continue
        package = package_match.group(1)
        if not package.startswith(prefix):
            continue

        depends_match = re.search(r"^Depends: ([^\n]+(?:\n .+)*)", stanza, re.MULTILINE)
        depends: List[str] = []
        if depends_match:
            depends_text = depends_match.group(1).replace("\n ", " ")
            for dep in depends_text.split(","):
                dep_name = dep.strip().split(" ", 1)[0]
                if dep_name.startswith(prefix):
                    depends.append(dep_name)
        packages[package] = depends
    return packages


def _fetch_apt_packages(
    cache_dir: Path, ubuntu_distro: str, architecture: str, ros_distro: str
) -> Dict[str, List[str]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"ros2_{ubuntu_distro}_{architecture}_Packages.gz"
    if not cache_file.exists():
        url = (
            "http://packages.ros.org/ros2/ubuntu/dists/"
            f"{ubuntu_distro}/main/binary-{architecture}/Packages.gz"
        )
        with urllib.request.urlopen(url, timeout=60) as response:
            cache_file.write_bytes(response.read())
    return _parse_apt_packages(cache_file.read_bytes(), ros_distro)


def _apt_to_module_name(package: str, ros_distro: str) -> str:
    return package.removeprefix(f"ros-{ros_distro}-").replace("-", "_")


def _has_target_roots(modules_dir: Path, module_name: str, version: str) -> bool:
    module_dir = modules_dir / module_name / version
    overlay_dir = module_dir / "overlay"
    return overlay_dir.exists() and any(overlay_dir.rglob("BUILD.bazel"))


def _target_patterns_for_package(
    modules_dir: Path, module_name: str, version: str
) -> List[str]:
    overlay_dir = modules_dir / module_name / version / "overlay"
    patterns: List[str] = []
    root_build_file = overlay_dir / "BUILD.bazel"
    if root_build_file.exists():
        root_build = root_build_file.read_text()
        if re.search(r"\bament_package\s*\(", root_build):
            patterns.append(f"@{module_name}//:ament_package")
        else:
            patterns.append(f"@{module_name}//:all")
        if re.search(r"\bdefault_generators\s*\(", root_build):
            patterns.extend(
                f"@{module_name}//:{target}"
                for target in GENERATED_ROOT_TARGETS
            )

    for build_file in sorted(overlay_dir.rglob("BUILD.bazel")):
        package = build_file.parent.relative_to(overlay_dir)
        if str(package) == ".":
            continue
        if SKIPPED_OVERLAY_PACKAGE_PARTS.intersection(package.parts):
            continue
        if INTERFACE_PACKAGE_PARTS.intersection(package.parts):
            patterns.append(f"@{module_name}//{package}:all")
    return patterns


def _is_gui_related(
    package: str,
    apt_packages: Dict[str, List[str]],
    seen: Set[str] | None = None,
) -> bool:
    if seen is None:
        seen = set()
    if package in seen:
        return False
    seen.add(package)

    if package.endswith("-dbgsym"):
        return True
    short_name = package.split("-", 2)[-1]
    if GUI_PACKAGE_RE.search(short_name):
        return True
    return any(
        _is_gui_related(dep, apt_packages, seen)
        for dep in apt_packages.get(package, [])
    )


def _is_module_gui_related(
    modules_dir: Path,
    module_name: str,
    release_packages: Dict[str, str],
    cache: Dict[str, bool],
    seen: Set[str] | None = None,
) -> bool:
    if module_name in cache:
        return cache[module_name]
    if seen is None:
        seen = set()
    if module_name in seen:
        return False
    seen.add(module_name)

    if GUI_PACKAGE_RE.search(module_name.replace("_", "-")):
        cache[module_name] = True
        return True

    version = release_packages.get(module_name)
    if version is None:
        cache[module_name] = False
        return False
    module_dot_bazel = modules_dir / module_name / version / "MODULE.bazel"
    if not module_dot_bazel.exists():
        cache[module_name] = False
        return False

    deps = scan_module_for_dependencies(module_dot_bazel, modules_dir)
    cache[module_name] = any(
        _is_module_gui_related(
            modules_dir, dep_name, release_packages, cache, seen
        )
        for dep_name in deps
    )
    return cache[module_name]


def calculate_packages_for_apt_slice(
    modules_dir: Path,
    module_names: Set[str],
    release_packages: Dict[str, str],
    apt_packages: Dict[str, List[str]],
    ros_distro: str,
    slice_name: str,
    provided_packages: Set[str] | None = None,
) -> List[str]:
    package_re = APT_SLICES[slice_name]
    provided_packages = provided_packages or set()
    packages: Set[str] = set()
    missing: Set[str] = set()
    skipped_gui = 0
    skipped_targetless = 0
    provided_external = 0
    gui_module_cache: Dict[str, bool] = {}

    for package in sorted(apt_packages):
        short_name = package.removeprefix(f"ros-{ros_distro}-")
        if not package_re.search(short_name):
            continue
        if _is_gui_related(package, apt_packages):
            skipped_gui += 1
            continue

        module_name = _apt_to_module_name(package, ros_distro)
        if _is_module_gui_related(
            modules_dir, module_name, release_packages, gui_module_cache
        ):
            skipped_gui += 1
            continue
        if module_name in module_names and module_name in release_packages:
            if not _has_target_roots(modules_dir, module_name, release_packages[module_name]):
                skipped_targetless += 1
                continue
            packages.add(module_name)
        elif module_name in provided_packages:
            provided_external += 1
        else:
            missing.add(module_name)

    if missing:
        print(
            f"Warning: apt slice '{slice_name}' has {len(missing)} packages without RCR modules: "
            + ", ".join(sorted(missing))
        )
    print(
        f"  apt:{slice_name}: {len(packages)} RCR packages "
        f"({len(missing)} missing, {skipped_gui} GUI/debug skipped, "
        f"{skipped_targetless} targetless skipped, "
        f"{provided_external} external provided)"
    )
    return sorted(packages)


def main():
    parser = argparse.ArgumentParser(
        description="Setup a Bazel workspace for testing ROS distributions in CI."
    )
    parser.add_argument(
        "--release",
        required=True,
        help="Bazel 'ros' module release version, e.g. lyrical.2026-06-08.rcr.1",
    )
    parser.add_argument(
        "--workspace-dir",
        type=Path,
        default=Path("workspace"),
        help="Directory to create the workspace in",
    )
    parser.add_argument(
        "--apt-distro",
        default="resolute",
        help="Ubuntu distro to read ROS buildfarm apt packages from, e.g. resolute",
    )
    parser.add_argument(
        "--apt-architecture",
        default="amd64",
        help="Architecture to read ROS buildfarm apt packages from, e.g. amd64",
    )
    parser.add_argument(
        "--apt-slice",
        action="append",
        choices=sorted(APT_SLICES),
        help="Also generate a target-pattern config from ros-<distro>-* apt packages",
    )
    args = parser.parse_args()

    # Locate directories
    workspace_root = Path(
        os.environ.get("BUILD_WORKSPACE_DIRECTORY", ".")
    ).resolve()
    modules_dir = workspace_root / "modules"
    ros_module_dir = modules_dir / "ros" / args.release

    if not ros_module_dir.exists():
        print(f"Error: ROS release module path {ros_module_dir} does not exist.", file=sys.stderr)
        sys.exit(1)

    # Scan release for dependencies
    print(f"Scanning ROS release '{args.release}' for packages...")
    packages = scan_module_for_dependencies(
        ros_module_dir / "MODULE.bazel", modules_dir
    )
    # Add ros and rosdistro to match the behavior of workspace_setup.py
    packages["ros"] = args.release
    packages["rosdistro"] = args.release
    print(f"Found {len(packages)} RCR packages in release.")

    # Detect variants
    variants: Dict[str, List[str]] = {}
    for variant_name, variant_package in VARIANTS.items():
        if variant_package not in packages:
            print(f"Warning: Variant package {variant_package} not found in release.")
            continue
        variants[variant_name] = calculate_packages_for_variant(
            modules_dir, variant_package, packages[variant_package]
        )
        print(f"  {variant_name}: {len(variants[variant_name])} packages")

    if args.apt_slice:
        apt_packages = _fetch_apt_packages(
            workspace_root / ".cache" / "apt",
            args.apt_distro,
            args.apt_architecture,
            args.release.split(".")[0],
        )
        module_names = {p.name for p in modules_dir.iterdir() if p.is_dir()}
        provided_packages = scan_module_for_dependencies(
            ros_module_dir / "MODULE.bazel",
            modules_dir,
            include_rcr=False,
            include_bcr=True,
        )
        rosdistro_version = packages.get("rosdistro")
        if rosdistro_version:
            provided_packages.update(
                scan_module_for_dependencies(
                    modules_dir / "rosdistro" / rosdistro_version / "MODULE.bazel",
                    modules_dir,
                    include_rcr=False,
                    include_bcr=True,
                )
            )
        for apt_slice in args.apt_slice:
            variants[apt_slice] = calculate_packages_for_apt_slice(
                modules_dir,
                module_names,
                packages,
                apt_packages,
                args.release.split(".")[0],
                apt_slice,
                set(provided_packages),
            )

    # Create workspace directory
    target_workspace = (workspace_root / args.workspace_dir).resolve()
    target_workspace.mkdir(parents=True, exist_ok=True)
    print(f"Creating workspace at {target_workspace}...")

    # Write BUILD.bazel
    with open(target_workspace / "BUILD.bazel", "w") as f:
        f.write(get_copyright_header())

    # Write ros-<variant>.txt files
    for variant_name, variant_packages in variants.items():
        with open(target_workspace / f"ros-{variant_name}.txt", "w") as f:
            if args.apt_slice and variant_name in args.apt_slice:
                target_patterns = [
                    pattern
                    for package in variant_packages
                    for pattern in _target_patterns_for_package(
                        modules_dir, package, packages[package]
                    )
                ]
            else:
                target_patterns = [f"@{p}//..." for p in variant_packages]
            f.write("\n".join(target_patterns))

    # Write MODULE.bazel
    with open(target_workspace / "MODULE.bazel", "w") as f:
        f.write(get_copyright_header())
        f.write("""module(
    name = "rcr-workspace",
)

# BCR deps

bazel_dep(name = "aspect_rules_py", version = "1.11.2")
bazel_dep(name = "llvm", version = "0.8.6")
bazel_dep(name = "platforms", version = "1.1.0")
bazel_dep(name = "protobuf", version = "35.0-rc1")
bazel_dep(name = "rules_cc", version = "0.2.18")
bazel_dep(name = "rules_go", version = "0.60.0")
bazel_dep(name = "rules_python", version = "1.9.0")
bazel_dep(name = "rules_rs", version = "0.0.56")
bazel_dep(name = "rules_rust", version = "0.69.0")
bazel_dep(name = "rules_shell", version = "0.8.0")

## RCR deps

""")
        f.write(f'bazel_dep(name = "ros", version = "{args.release}")\n\n')
        for name in sorted(packages.keys()):
            if name != "ros":
                f.write(f'bazel_dep(name = "{name}", version = "{packages[name]}")\n')

        f.write("""
## BOOTSTRAP

# Register our hermetic compiler (clang)
register_toolchains("@llvm//toolchain:all")

# LLVM is hermetic by default and therefore it cannot see system frameworks.
# We need to explicitly tell it where to find them. IOKit is needed for FastRTPS
# to work on macOS, and OpenGL is needed for visualization tools like rviz2.
# The rest are common frameworks needed by OpenCV.
osx = use_extension("@llvm//extensions:osx.bzl", "osx")
osx.frameworks(
    names = [
        "AVFAudio",
        "AVFoundation",
        "AVRouting",
        "Accelerate",
        "AppKit",
        "ApplicationServices",
        "AudioToolbox",
        "CFNetwork",
        "CloudKit",
        "Cocoa",
        "ColorSync",
        "CoreAudio",
        "CoreAudioTypes",
        "CoreData",
        "CoreFoundation",
        "CoreGraphics",
        "CoreImage",
        "CoreLocation",
        "CoreMIDI",
        "CoreMedia",
        "CoreServices",
        "CoreText",
        "CoreVideo",
        "DiskArbitration",
        "FontServices",
        "Foundation",
        "IOKit",
        "IOSurface",
        "ImageIO",
        "Kernel",
        "MediaToolbox",
        "Metal",
        "OSLog",
        "OpenGL",
        "PrintCore",
        "QuartzCore",
        "Security",
        "Symbols",
        "SystemConfiguration",
        "UniformTypeIdentifiers",
        "_LocationEssentials",
    ],
)

# Python / pip

python = use_extension("@rules_python//python/extensions:python.bzl", "python")
python.toolchain(
    is_default = True,
    python_version = "3.12",
)

pip_ros = use_extension("@rosdistro//python:defs.bzl", "pip_ros")
use_repo(pip_ros, "pip_ros")

# Rust / crates

rust = use_extension("@rules_rust//rust:extensions.bzl", "rust")
rust.toolchain(
    edition = "2021",
    versions = ["1.85.0"],
)
""")

    # Write .bazelrc
    distro = args.release.split(".")[0]
    variant_configs = "\n".join(
        f"common:{variant_name} --target_pattern_file=ros-{variant_name}.txt"
        for variant_name in variants
    )
    with open(target_workspace / ".bazelrc", "w") as f:
        f.write(BAZELRC_TEMPLATE.format(distro=distro, variants=variant_configs))

    print("Workspace setup complete.")

if __name__ == "__main__":
    main()
