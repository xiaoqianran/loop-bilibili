# Peer AI Userscript Practices

Research snapshot for optimizing `bili-subbatch.user.js` (2026-08).

Sources reviewed:

| Script / project | Where | Takeaways |
|------------------|--------|-----------|
| **chatGPT tools Plus（修改版）** | GreasyFork 459997 | `GM_xmlhttpRequest` + `responseType: "stream"` + `onloadstart` → `stream.response.getReader()`; keep `abortXml` handle; `stream: true` |
| **Fastmail AI Assistant** | GreasyFork 552191 | `GM_setValue` / `GM_getValue` for API key & endpoint; menu commands for settings |
| **YouTube Video Summarizer** | gist strickvl | Site-page UI + local key storage; GM for cross-origin API; transcript → prompt → summary |
| **Netflix subtitles translator** | dariodf/netflix_subtitles_translator | Prefer page hooks; `GM_xmlhttpRequest` only when page `fetch` is locked |
| **ChatGPT scroll / stick-bottom patterns** | community gists + SO | Last-node `scrollIntoView`; gap-to-bottom threshold; pause when user scrolls up |

## Practices we adopt

1. **Request path hierarchy**  
   Page-context `fetch` + `ReadableStream` first (same-origin CORS often works for public OpenAI-compatible gateways — verified via opencli on bilibili.com).  
   Fallback: `GM_xmlhttpRequest` with **no `timeout` field** (TM: `timeout` forces fetch mode and breaks `onprogress` / can fire false `ontimeout`).  
   Optional GM stream: `responseType: "stream"` + reader when available (GreasyFork 459997).

2. **Streaming UI**  
   Coalesce paints with `requestAnimationFrame`.  
   Stick-to-bottom while `scrollHeight - scrollTop - clientHeight < threshold` (≈48px).  
   User scroll-up clears stick; explicit “粘底” re-enables.  
   Bottom **anchor** + `scrollIntoView({ block: "end" })` (more reliable than raw `scrollTop` alone).

3. **Config storage**  
   Prefer `GM_setValue` / `GM_getValue` when granted (survives clearer isolation than page `localStorage` in some managers); fall back to `localStorage`.  
   Never require committing API keys; defaults optional.

4. **Abort**  
   Single active request handle: page `AbortController` **or** GM `abort()`.  
   Stop button only aborts AI request (does not cancel unrelated subtitle batch).

5. **Delta fields**  
   Parse both `delta.content` and `delta.reasoning` / `reasoning_content` (reasoning models / NewAPI).

6. **Token control**  
   Truncate long transcripts before prompt injection (peer summarizers limit input size).

## Mapping into this repo

| Practice | Code |
|----------|------|
| Page fetch stream first | `requestChatViaPageFetch` |
| GM fallback no timeout + stream reader | `requestChatViaGm` |
| Stick-bottom + pause | `shouldStickBottom` / `bindAiScrollBehavior` / `scrollAiToBottom` |
| rAF stream paint | `paintAiStreamText` |
| Storage | `loadAiConfig` / `saveAiConfig` with GM_* then localStorage |
| Abort | `state.aiAbortController` + `state.aiXhr` |
| Reasoning deltas | `extractAssistantText` / `extractFromChoice` |
| Truncate | `truncateForAi` |
