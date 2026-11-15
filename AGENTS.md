# Fork maintenance

This fork follows upstream `main`. Local work is committed directly on `main`.
Home-server deployment configuration and credentials live outside this public
repository; never copy runtime secrets or personal conversation data here.

The saved-chat navigation change is in
`src/lib/components/chat/Chat.svelte` and `src/lib/utils/chatSnapshot.ts`.
It joins the realtime room before the initial snapshot, loads tags in parallel,
and starts task reconciliation after the snapshot arrives. Retain reconnect,
refused/late-join refreshes, abort handling and the live task-state guard.
Do not remove reasoning/tool history or cache authenticated snapshots globally.

Validate frontend changes with:

```bash
ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --force
npm run test:frontend -- --run
npm run check
npm run build
```

Use the existing upstream Dockerfile for source builds; `USE_CUDA=true` selects
the home deployment's CUDA variant. Home Compose orchestration remains in
`/home/adrian/docker` and points its build context to this checkout.
