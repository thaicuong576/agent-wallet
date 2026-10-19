# Third-Party Notices

BaKhi Wallet includes code adapted from the following open-source projects under their respective permissive licenses:

---

## 1. Azure/fetch-event-source

- **Project:** [Azure/fetch-event-source](https://github.com/Azure/fetch-event-source)
- **License:** MIT License
- **Copyright:** (c) Microsoft Corporation.
- **Adapted in:** `ui/lib/sse.js`
- **What was adapted:** The SSE line and event parser (`getBytes`, `getLines`, `getMessages`) and retry logic from `src/parse.ts` and `src/fetch.ts` were adapted into plain, zero-dependency JavaScript for `ui/lib/sse.js`. It provides support for custom headers (including `X-AW-CSRF`), `Last-Event-ID` resumption, and `AbortController` cancellation for streaming chat job progress without third-party dependencies.

### MIT License

```
MIT License

Copyright (c) Microsoft Corporation.

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
SOFTWARE
```

---

## 2. sysid/sse-starlette

- **Project:** [sysid/sse-starlette](https://github.com/sysid/sse-starlette)
- **License:** BSD 3-Clause License
- **Copyright:** (c) 2020, sysid
- **Adapted in:** `api/sse.py`
- **What was adapted:** The event formatting design (`ServerSentEvent` encoding with `event:`, `data:` JSON, `id:`, `retry:`, comments/pings), comment ping keep-alives, and client disconnect handling ideas were adapted into `api/sse.py` on top of Starlette's `StreamingResponse`, without adding any new runtime dependency.

### BSD 3-Clause License

```
Copyright © 2020, sysid.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## 3. Token marks

- **Files:** `ui/assets/tokens/*.svg` (USDC, USDT, ETH, WBTC, LINK)
- **Note:** These marks belong to their respective issuers. They are shown only to identify the testnet assets in the interface and do not imply endorsement.

## 4. Python dependencies

Runtime and development dependencies are listed in `requirements.txt` and `requirements-dev.txt`. They are installed from PyPI under their own licenses and are not vendored into this repository.
