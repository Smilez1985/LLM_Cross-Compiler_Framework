---
title: Downloading gated models from Hugging Face
lang: en
---
# Downloading gated models from Hugging Face

Some model repositories are gated: the authors ask you to accept a licence or share contact details before the
files can be downloaded. Without access a download fails with `401 Client Error` or `403 Forbidden`, and the
error names a `GatedRepoError`: "Access to model ... is restricted".

1. Sign in on the Hugging Face website, open the model page and accept the conditions. Some authors approve
   requests by hand; the page shows when access is granted.
2. Create an access token in your account settings. A fine-grained token needs the permission to read public
   gated repositories; a classic read token works as well.
3. Give the token to the framework once: `llm-cli secrets set hf_token`, or the token field of the Hugging Face
   window in the GUI. It is kept in the system keyring and passed to the build container for the download only.

A 401 error means that no token, or an invalid one, was sent. A 403 error with a valid token means that access
to this repository has not been granted (yet).
