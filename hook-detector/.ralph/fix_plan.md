# Ralph Fix Plan

## High Priority
- [x] Set up basic project structure and build system
- [x] Define core data structures and types
- [x] Implement basic input/output handling
- [x] Create test framework and initial tests

## Medium Priority
- [x] Add error handling and validation
- [x] Implement core business logic
- [x] Add configuration management
- [ ] Create user documentation

## Low Priority
- [ ] Performance optimization
- [ ] Extended feature set
- [ ] Integration with external services
- [ ] Advanced error recovery

## Completed
- [x] Project initialization
- [x] requirements.txt with all dependencies
- [x] config.py — all PRD constants
- [x] audio_capture.py — BlackHole capture with chunking, device detection, error handling
- [x] transcriber.py — Whisper wrapper with rolling 180s buffer
- [x] hook_generator.py — Ollama API with prompt, dedup, parsing, health checks
- [x] web_server.py — FastAPI app with all 6 API endpoints
- [x] templates/index.html — Minimal responsive UI with polling, start/stop, transcript toggle
- [x] main.py — Full orchestration with --transcribe-only mode
- [x] tests/test_hook_generator.py — Unit tests for hook parsing, generation, Ollama checks
- [x] tests/test_config.py — Config constant verification

## In Progress
- [ ] Install dependencies (pip3 install -r requirements.txt)
- [ ] Run pytest to validate tests pass
- [ ] Manual integration test with BlackHole + Ollama

## Notes
- Python 3.11+ required (system Python is 3.9.6 — need Homebrew Python)
- pywhispercpp needs whisper.cpp which may require cmake
- BlackHole 2ch must be installed and configured as audio output
- Ollama must be running with qwen3:30b model loaded
- All 8 PRD test cases are implementable via CLI flags on each module
