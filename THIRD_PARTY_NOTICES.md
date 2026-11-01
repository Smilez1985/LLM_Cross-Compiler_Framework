# Third-party notices

Parts of this project are adapted from other open-source projects. Their licences require the notices
below to be kept with those parts.

## llama.cpp (built from source)

Source: https://github.com/ggml-org/llama.cpp, pinned by commit in `configs/llama_runtime.yml`
(`llama_cpp.source`).

The framework contains no llama.cpp code. It fetches the pinned commit and builds it on the user's machine
(`orchestrator/runtime/build/`); the builds it makes (Ditto's local server, the conversion tools in target
images, device runtimes) are llama.cpp binaries under llama.cpp's MIT licence, so the notice below goes with
them wherever they are passed on. A build imported from another machine of the user is the same. Upstream's
official prebuilt archives are downloaded only when the user asks for them (`llm-cli ditto runtime install
--prebuilt`), from upstream's release page, under the same licence. Compilers, CMake, Ninja, the packages of the
builder image's base and, later, GPU toolkits are installed by the user from their vendors under the vendors' own
licences; the framework never accepts a licence on the user's behalf. The GPU target template downloads NVIDIA's
CUDA components (CUDA Toolkit licence, LICENSE in each archive) while the user builds that image; the framework ships
none of them.

A device tarball (`llm-cli device runtime build`) carries this notice as `LICENSE-llama.cpp`. A tarball of an
OpenMP build also holds `lib/libgomp.so.1`, the OpenMP runtime from the GCC packages of the builder image's base
distribution, under the GNU GPL version 3 with the GCC Runtime Library Exception; its source is in that
distribution's archive. The framework passes none of these files on itself: tarballs are built on the user's
machine for the user's devices, and whoever passes one on to others passes these licences on with it.

```
MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## whisper.cpp (built from source)

Source: https://github.com/ggml-org/whisper.cpp, pinned by commit in `configs/llama_runtime.yml`
(`whisper_cpp.source`).

The framework contains no whisper.cpp code. It fetches the pinned commit and builds it on the user's machine for a
board, through the same self-build path as llama.cpp (`orchestrator/runtime/build/projects.py`); the board package
(the bundle block `stt-whisper-cpp`) holds whisper.cpp binaries under whisper.cpp's MIT licence and carries its
notice as `LICENSE-whisper.cpp`. Its text is the MIT licence above, with the copyright line of the whisper.cpp
repository (`Copyright (c) 2023-2026 The ggml authors`). The speech models a person picks come under their own
licences; the person names each one in the bundle plan.

## SPDX License List data (canonical licence texts)

Source: https://github.com/spdx/license-list-data, version 3.29.0 (tag `v3.29.0`), the folder `text/`.

`orchestrator/data/licenses/<SPDX id>.txt` holds the texts of the licences that ask for their text or permission
notice with every copy (MIT, BSD-2-Clause, BSD-3-Clause, ISC, Apache-2.0, MPL-2.0, CC BY and CC BY-SA 1.0 to 4.0),
unchanged; `licenses.json` next to them records the version, where each text came from and its sha256. A knowledge
pack ships the texts its pages need (`orchestrator/rag/pack_licences.py`). The SPDX License List data is under the
Creative Commons Zero v1.0 Universal dedication (CC0-1.0); each licence text stays the text of its own licence.

## aiomqtt and Eclipse Paho MQTT (the build doorbell)

Sources: https://github.com/empicano/aiomqtt (version 2.5.1, BSD-3-Clause) and
https://github.com/eclipse-paho/paho.mqtt.python (version 2.1.0, dual licensed EPL-2.0 OR BSD-3-Clause under the
Eclipse Distribution License 1.0; this project uses it under the BSD-3-Clause terms of the EDL 1.0).

The framework contains no code of either project. Both are installed as Python dependencies (exact pins in
`pyproject.toml`) and imported only when the build doorbell sends a message (`orchestrator/Core/doorbell.py`).

aiomqtt licence:

```
Copyright 2020 (c) SBT Instruments

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

Eclipse Paho MQTT Python client, Eclipse Distribution License 1.0 (BSD-3-Clause):

```
Eclipse Distribution License - v 1.0

Copyright (c) 2007, Eclipse Foundation, Inc. and its licensors.

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

  Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

  Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

  Neither the name of the Eclipse Foundation, Inc. nor the names of its
  contributors may be used to endorse or promote products derived from this
  software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## rag4ll

Source: https://github.com/Smilez1985/rag4ll

Adapted parts (each marked with an "Adapted from rag4ll" comment in the code):

- `orchestrator/rag/retrieval.py`: the cross-encoder reranker (from `rag4ll/reranking/rerank_fastembed.py`)
  and the excerpt budget packer (from `rag4ll/retrieval/budget.py`).
- `orchestrator/Core/community_pack.py`: the pack manifest (from `rag4ll/sidecar/manifest.py`) and the
  privacy scrub (from `rag4ll/mojo/redact.py`).
- `orchestrator/utils/announce.py`: the best-effort shape of the speech call, which never fails the caller
  (from `play()` in `scripts/rag-usb-watcher`).

```
MIT License

Copyright (c) 2026 Smilez1985

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
