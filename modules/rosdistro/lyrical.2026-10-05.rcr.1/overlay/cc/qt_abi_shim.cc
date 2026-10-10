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

// The prebuilt Qt is built against libstdc++, while this repository builds
// with libc++. Qt's inline QString::toStdString() calls the out-of-line
// QByteArray::toStdString(), which libQt6Core only exports under the libstdc++
// (abi:cxx11) spelling, so every Qt consumer would fail to link. Defining it
// here provides the libc++ spelling.

#include <QtCore/QByteArray>

#include <cstddef>
#include <string>

std::string QByteArray::toStdString() const
{
  return std::string(constData(), static_cast<std::size_t>(size()));
}
