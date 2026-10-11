# Third-party notices

Parts of this project are adapted from other open-source projects. Their licences require the notices
below to be kept with those parts.

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
