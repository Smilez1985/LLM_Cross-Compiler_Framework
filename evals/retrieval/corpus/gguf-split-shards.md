---
title: Split and merged GGUF files
lang: en
---
# Split and merged GGUF files

Large models are often published as several GGUF shards named like `model-00001-of-00004.gguf`. llama.cpp loads
such a model from its first shard and finds the others in the same folder, so the files do not have to be
joined for inference.

To create shards, for example to stay below an upload limit:

```
llama-gguf-split --split --split-max-size 4G model-Q4_K_M.gguf model-Q4_K_M
```

To join shards into one file, pass the first shard and the output name:

```
llama-gguf-split --merge model-Q4_K_M-00001-of-00004.gguf model-Q4_K_M.gguf
```

Shards must not be concatenated with `cat`: each shard has its own header, and a file joined that way does not
load. Keep all shards of one model together; a missing shard is reported when the model is loaded.
