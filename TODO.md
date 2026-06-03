# TODO

- [x] Fix `LogsWindow.load_logs()` logic (remove nested-loop bug, sort once, populate table once)

- [x] Move log fetching/building to background thread (QThread) to prevent UI freeze

- [ ] Limit logs shown (optional, e.g. latest 500) if needed after testing

- [ ] Smoke test: open Activity Logs and double-click screenshot rows

