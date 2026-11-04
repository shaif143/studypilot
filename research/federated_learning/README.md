# Federated learning — design notes

**Status: not implemented.** No training runs, no model updates are computed,
and nothing is transmitted anywhere. This directory holds the design and the
threat model that must come first. The settings switch records a preference for
a capability that does not yet exist, and the UI says so.

This is deliberate. The brief asks for federated learning to be *designed*
without making the application depend on it, and warns against claiming
federated privacy without security controls. Shipping an aggregation client
before the threat model is worked out would be exactly that.

---

## Why it is interesting here

Every student's learning history is private, and individually it is small. The
things worth learning across students are population-level:

| Candidate task | Why federation helps | Why it is hard |
| --- | --- | --- |
| Question difficulty estimation | Difficulty is a property of the item, not the student; one student gives too few observations | Item identity differs across institutions |
| Mastery prediction | Learning curves generalise across students | Non-IID: each student sees a different course |
| Activity recommendation | "What helped someone stuck here" needs many students | Reward is delayed and noisy |
| Retrieval ranking | Which passage answers which question shape | Documents are private and unshared |

The honest assessment: **question difficulty estimation** is the strongest
candidate. It has a clean, low-dimensional target, tolerates noise, and the
update reveals little about any individual. Mastery prediction is next.

---

## Intended shape

```text
device                                        server
───────────────────────────────────────       ──────────────────────────
local history (never leaves)
   │
   ├─ train locally (PyTorch, CPU, minutes)
   │
   ├─ clip the update, add calibrated noise
   │
   └─ encrypt under secure aggregation ──────► sum only; no individual
                                                update is ever decrypted
                                                     │
                      ◄───────────────────────── updated global model
```

- **Flower** for orchestration, **PyTorch** for the local model.
- **FedAvg** as the research baseline, with FedProx evaluated for non-IID data.
- Participation strictly opt-in, revocable, and off by default.
- Raw documents and learning histories are never uploaded under any
  configuration.

---

## Threat model (the work that must happen first)

| Threat | Mitigation | Status |
| --- | --- | --- |
| Server reads an individual update | Secure aggregation: the server can only decrypt the sum | Not implemented |
| Update leaks training data (gradient inversion) | Clip updates; add DP noise; restrict to low-dimensional tasks | Not implemented |
| A participant infers another's data from the global model | Central DP with an accounted ε budget | Not implemented |
| Malicious participants poison the model | Robust aggregation (trimmed mean, Krum); contribution bounds | Not implemented |
| Dropout correlates with a student's circumstances | Dropout-tolerant aggregation; no penalty for leaving | Not implemented |
| Participation itself is identifying | Participation must not be observable from the client's behaviour | Not designed |
| Battery and data cost | Train only on mains power; bound update size | Not designed |

**No claim of anonymity will be made.** Federated learning moves computation,
not risk. A model update can leak information about the data that produced it,
which is why secure aggregation and differential privacy are prerequisites
rather than enhancements. Any eventual ε will be stated numerically, with what
it does and does not mean.

---

## What exists in the codebase today

Only the seam. `app/learning/mastery.py` keeps the knowledge model behind plain
functions operating on a `MasteryEstimate`, so a learned predictor can be
substituted without touching the API or the UI. `UserSettings` carries
`allow_federated_participation`, default `False`, and the privacy screen
describes it accurately as not implemented.

## Before any of this ships

1. A written threat model with mitigations mapped to implementations.
2. Secure aggregation working, with a test that the server cannot recover an
   individual update.
3. A DP accountant with a published ε, and a measurement of its utility cost.
4. A gradient-inversion attempt against the chosen task, documented.
5. A consent flow that explains the actual guarantee, not a reassuring summary.
6. An audited way to withdraw, including what withdrawal cannot undo.
