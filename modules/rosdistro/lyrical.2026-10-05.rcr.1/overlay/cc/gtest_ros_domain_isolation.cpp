// Copyright 2026 Open Source Robotics Foundation, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Gives every test that links @rosdistro//cc:googletest its own ROS domain,
// the Bazel counterpart of upstream's ament_add_ros_isolated_gtest (which
// runs tests through domain_coordinator). Without it, tests running in
// parallel see each other's ROS entities whenever they share a network, e.g.
// through a common rmw_zenoh_cpp router.
//
// Like domain_coordinator, a domain is reserved by binding the localhost TCP
// port 22119 + domain_id for the lifetime of the test process. Nothing is
// changed if ROS_DOMAIN_ID is already set.

#if !defined(_WIN32)

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdlib>
#include <functional>
#include <string>

namespace {

constexpr int kBasePort = 22119;
constexpr int kNumDomains = 100;  // Domains 1..100, as domain_coordinator.

bool reserve(int domain_id)
{
  const int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    return false;
  }
  fcntl(fd, F_SETFD, FD_CLOEXEC);
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(kBasePort + domain_id));
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (bind(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) != 0) {
    close(fd);
    return false;
  }
  // Keep the socket open (and the domain reserved) until the process exits.
  return true;
}

void isolate_ros_domain()
{
  const char * current = std::getenv("ROS_DOMAIN_ID");
  if (current != nullptr && current[0] != '\0') {
    return;
  }
  // Start from a per-test offset so parallel tests rarely contend.
  const char * target = std::getenv("TEST_TARGET");
  const char * shard = std::getenv("TEST_SHARD_INDEX");
  const size_t start = std::hash<std::string>{}(
    std::string(target ? target : "") + "#" + (shard ? shard : "") + "#" +
    std::to_string(getpid()));
  for (int i = 0; i < kNumDomains; ++i) {
    const int domain_id = 1 + static_cast<int>((start + i) % kNumDomains);
    if (reserve(domain_id)) {
      setenv("ROS_DOMAIN_ID", std::to_string(domain_id).c_str(), 1);
      return;
    }
  }
}

const bool g_ros_domain_isolated = (isolate_ros_domain(), true);

}  // namespace

#endif  // !defined(_WIN32)
