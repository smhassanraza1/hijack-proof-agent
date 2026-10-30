# Third-party notices

## AgentDojo

This repository uses and adapts material from AgentDojo (https://github.com/ethz-spylab/agentdojo;
Debenedetti et al., "AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and
Defenses for LLM Agents", NeurIPS 2024 Datasets and Benchmarks, arXiv:2406.13352):

- `evals/cases.jsonl`, `evals/cases_registered.jsonl` and the traces under `results/` contain AgentDojo v1.2.1 user
  tasks, attacker goals, environment data and attack-template text;
- `agent/llm.py` replaces AgentDojo's `LocalLLM` element and reuses its tool-calling prompt format;
- `evals/defenses.py` builds AgentDojo's built-in defences, adapting its tool filter.

AgentDojo is distributed under the following licence:

```
MIT License

Copyright (c) 2024 Edoardo Debenedetti, Jie Zhang, Mislav Balunovic, Luca Beurer-Kellner, Marc Fischer, and Florian Tramèr

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
