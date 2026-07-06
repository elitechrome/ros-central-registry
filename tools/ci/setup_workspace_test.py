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

import gzip
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.ci.setup_workspace import _is_gui_related
from tools.ci.setup_workspace import _parse_apt_packages
from tools.ci.setup_workspace import _target_patterns_for_package
from tools.ci.setup_workspace import calculate_packages_for_apt_slice


class TestSetupWorkspace(unittest.TestCase):

    def _make_module(self, root: Path, name: str, version: str, targetful: bool = True):
        module_dir = root / name / version
        module_dir.mkdir(parents=True)
        if targetful:
            overlay_dir = module_dir / "overlay"
            overlay_dir.mkdir()
            (overlay_dir / "BUILD.bazel").write_text("")

    def test_parse_apt_packages_filters_to_ros_distro(self):
        data = gzip.compress(
            b"""Package: ros-lyrical-nav-msgs
Depends: ros-lyrical-std-msgs, libc6

Package: ros-kilted-nav-msgs
Depends: ros-kilted-std-msgs
"""
        )

        self.assertEqual(
            _parse_apt_packages(data, "lyrical"),
            {"ros-lyrical-nav-msgs": ["ros-lyrical-std-msgs"]},
        )

    def test_gui_related_walks_ros_dependencies(self):
        packages = {
            "ros-lyrical-mrpt-nav": ["ros-lyrical-mrpt-viz"],
            "ros-lyrical-mrpt-viz": [],
            "ros-lyrical-rqml": ["ros-lyrical-qml6-ros2-plugin"],
            "ros-lyrical-qml6-ros2-plugin": [],
        }

        self.assertTrue(_is_gui_related("ros-lyrical-mrpt-nav", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-mrpt-libgui", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-webots-ros2-importer", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-yasmin-demos", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-yasmin-viewer", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-event-camera-renderer", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-gz-ogre-next-vendor", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-gz-rendering-vendor", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-mrpt-libopengl", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-qml6-ros2-plugin", packages))
        self.assertTrue(_is_gui_related("ros-lyrical-rqml", packages))
        self.assertTrue(
            _is_gui_related("ros-lyrical-rmf-visualization-floorplans", packages)
        )
        self.assertTrue(_is_gui_related("ros-lyrical-turtlesim", packages))
        self.assertFalse(
            _is_gui_related("ros-lyrical-mqtt-client-interfaces", packages)
        )
        self.assertFalse(_is_gui_related("ros-lyrical-visualization-msgs", packages))
        self.assertFalse(
            _is_gui_related("ros-lyrical-rmf-visualization-msgs", packages)
        )

    def test_navigation_slice_maps_existing_non_gui_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir)
            self._make_module(modules_dir, "nav_msgs", "1.0.0")

            apt_packages = {
                "ros-lyrical-auto-apms-behavior-tree": [],
                "ros-lyrical-behaviortree-cpp": [],
                "ros-lyrical-moveit-resources-prbt-support": [],
                "ros-lyrical-nav-msgs": [],
                "ros-lyrical-mrpt-nav": ["ros-lyrical-mrpt-viz"],
                "ros-lyrical-mrpt-viz": [],
                "ros-lyrical-nav-msgs-dbgsym": [],
            }

            self.assertEqual(
                calculate_packages_for_apt_slice(
                    modules_dir=modules_dir,
                    module_names={
                        "auto_apms_behavior_tree",
                        "behaviortree_cpp",
                        "moveit_resources_prbt_support",
                        "mrpt_nav",
                        "nav_msgs",
                    },
                    release_packages={
                        "auto_apms_behavior_tree": "1.0.0",
                        "behaviortree_cpp": "1.0.0",
                        "moveit_resources_prbt_support": "1.0.0",
                        "mrpt_nav": "1.0.0",
                        "nav_msgs": "1.0.0",
                    },
                    apt_packages=apt_packages,
                    ros_distro="lyrical",
                    slice_name="navigation",
                ),
                ["nav_msgs"],
            )

    def test_target_patterns_use_overlay_packages_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir)
            self._make_module(modules_dir, "nav_msgs", "1.0.0")
            root_build = modules_dir / "nav_msgs" / "1.0.0" / "overlay" / "BUILD.bazel"
            root_build.write_text("ament_package(name = \"ament_package\")\n")
            msg_dir = modules_dir / "nav_msgs" / "1.0.0" / "overlay" / "msg"
            msg_dir.mkdir()
            (msg_dir / "BUILD.bazel").write_text("")
            test_dir = modules_dir / "nav_msgs" / "1.0.0" / "overlay" / "test"
            test_dir.mkdir()
            (test_dir / "BUILD.bazel").write_text("")

            self.assertEqual(
                _target_patterns_for_package(modules_dir, "nav_msgs", "1.0.0"),
                ["@nav_msgs//:ament_package", "@nav_msgs//msg:all"],
            )

    def test_target_patterns_include_generated_interface_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir)
            self._make_module(modules_dir, "nav_msgs", "1.0.0")
            root_build = modules_dir / "nav_msgs" / "1.0.0" / "overlay" / "BUILD.bazel"
            root_build.write_text(
                "default_generators(package_xml = \"package.xml\")\n"
                "ament_package(name = \"ament_package\")\n"
            )
            srv_dir = modules_dir / "nav_msgs" / "1.0.0" / "overlay" / "srv"
            srv_dir.mkdir()
            (srv_dir / "BUILD.bazel").write_text("")
            test_msg_dir = (
                modules_dir / "nav_msgs" / "1.0.0" / "overlay" / "test" / "msg"
            )
            test_msg_dir.mkdir(parents=True)
            (test_msg_dir / "BUILD.bazel").write_text("")
            nested_action_dir = (
                modules_dir
                / "nav_msgs"
                / "1.0.0"
                / "overlay"
                / "nested_sub_dir"
                / "action"
            )
            nested_action_dir.mkdir(parents=True)
            (nested_action_dir / "BUILD.bazel").write_text("")

            self.assertEqual(
                _target_patterns_for_package(modules_dir, "nav_msgs", "1.0.0"),
                [
                    "@nav_msgs//:ament_package",
                    "@nav_msgs//:c",
                    "@nav_msgs//:cc",
                    "@nav_msgs//:idl",
                    "@nav_msgs//:proto",
                    "@nav_msgs//:py",
                    "@nav_msgs//:rs",
                    "@nav_msgs//nested_sub_dir/action:all",
                    "@nav_msgs//srv:all",
                ],
            )

    def test_nongui_slice_maps_all_existing_non_gui_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir)
            self._make_module(modules_dir, "marker_user", "1.0.0")
            self._make_module(modules_dir, "mqtt_client_interfaces", "1.0.0")
            self._make_module(modules_dir, "nav_msgs", "1.0.0")
            self._make_module(modules_dir, "robot_description", "1.0.0")
            self._make_module(modules_dir, "rviz2", "1.0.0")
            self._make_module(modules_dir, "visualization_msgs", "1.0.0")
            (
                modules_dir / "marker_user" / "1.0.0" / "MODULE.bazel"
            ).write_text('bazel_dep(name = "visualization_msgs", version = "1.0.0")\n')
            (
                modules_dir / "robot_description" / "1.0.0" / "MODULE.bazel"
            ).write_text('bazel_dep(name = "rviz2", version = "1.0.0")\n')

            apt_packages = {
                "ros-lyrical-marker-user": [],
                "ros-lyrical-mqtt-client-interfaces": [],
                "ros-lyrical-nav-msgs": [],
                "ros-lyrical-robot-description": [],
                "ros-lyrical-rqt-gui": [],
                "ros-lyrical-rqt-gui-dbgsym": [],
            }

            self.assertEqual(
                calculate_packages_for_apt_slice(
                    modules_dir=modules_dir,
                    module_names={
                        "marker_user",
                        "mqtt_client_interfaces",
                        "nav_msgs",
                        "robot_description",
                        "rqt_gui",
                        "rviz2",
                        "visualization_msgs",
                    },
                    release_packages={
                        "marker_user": "1.0.0",
                        "mqtt_client_interfaces": "1.0.0",
                        "nav_msgs": "1.0.0",
                        "robot_description": "1.0.0",
                        "rqt_gui": "1.0.0",
                        "rviz2": "1.0.0",
                        "visualization_msgs": "1.0.0",
                    },
                    apt_packages=apt_packages,
                    ros_distro="lyrical",
                    slice_name="nongui",
                ),
                ["marker_user", "mqtt_client_interfaces", "nav_msgs"],
            )

    def test_nongui_slice_skips_targetless_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir)
            self._make_module(modules_dir, "targetless_msgs", "1.0.0", targetful=False)

            self.assertEqual(
                calculate_packages_for_apt_slice(
                    modules_dir=modules_dir,
                    module_names={"targetless_msgs"},
                    release_packages={"targetless_msgs": "1.0.0"},
                    apt_packages={"ros-lyrical-targetless-msgs": []},
                    ros_distro="lyrical",
                    slice_name="nongui",
                ),
                [],
            )

    def test_nongui_slice_counts_external_provided_packages_as_covered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            modules_dir = Path(temp_dir)
            self._make_module(modules_dir, "nav_msgs", "1.0.0")

            output = io.StringIO()
            with redirect_stdout(output):
                packages = calculate_packages_for_apt_slice(
                    modules_dir=modules_dir,
                    module_names={"nav_msgs"},
                    release_packages={"nav_msgs": "1.0.0"},
                    apt_packages={
                        "ros-lyrical-fastdds": [],
                        "ros-lyrical-nav-msgs": [],
                    },
                    ros_distro="lyrical",
                    slice_name="nongui",
                    provided_packages={"fastdds"},
                )

            self.assertEqual(packages, ["nav_msgs"])
            self.assertIn("0 missing", output.getvalue())
            self.assertIn("1 external provided", output.getvalue())


if __name__ == "__main__":
    unittest.main()
