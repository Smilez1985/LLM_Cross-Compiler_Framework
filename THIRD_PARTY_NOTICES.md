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
licences; the framework never accepts a licence on the user's behalf.

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
