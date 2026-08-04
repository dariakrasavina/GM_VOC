# GM VOC POC — Architecture

Simple diagrams of how the pieces fit together. They render on GitHub and in any
Mermaid viewer (e.g. https://mermaid.live).

There are **three tracks**. Tracks 1 and 2 both do **topic tagging** and are
**compared** so GM can pick the best. Track 3 does **sentiment** and is used
**alongside** the winner.

---

## 1. The big picture

```mermaid
flowchart LR
    DATA["Call transcripts<br/>(one row per sentence)"]

    subgraph TOPICS["Topic tagging — compare & pick"]
        T1["Track 1<br/>Rule engine"]
        T2["Track 2<br/>AI functions"]
    end

    T3["Track 3<br/>Sentiment model"]
    OUT["Topic + Sentiment<br/>per sentence"]

    DATA --> T1
    DATA --> T2
    DATA --> T3
    T1 --> CMP{{"Compare<br/>rules vs AI"}}
    T2 --> CMP
    CMP --> OUT
    T3 --> OUT
```

---

## 2. Track 1 — Rule engine (topic tagging)

```mermaid
flowchart LR
    RULES["rules.json<br/>(GM's topic rules)"]
    IN["Sentences"]
    ENGINE["Rule engine<br/>keyword / phrase / boolean match"]
    TAGS["Topic tags<br/>+ which words matched"]

    RULES --> ENGINE
    IN --> ENGINE --> TAGS
```

Deterministic and explainable. The rules come from GM's workbook; the engine
runs the same way locally and on Databricks.

---

## 3. Track 2 — AI functions (topic tagging + theme discovery)

```mermaid
flowchart LR
    IN["Sentences"]
    CLASSIFY["ai_classify<br/>pick one of the 4 topics"]
    DISCOVER["embeddings + clustering<br/>find brand-new themes"]
    TAGS["AI topic tags"]
    THEMES["Discovered themes"]

    IN --> CLASSIFY --> TAGS
    IN --> DISCOVER --> THEMES
```

An LLM assigns topics; a separate step groups sentences into themes nobody
defined. Flexible, but costs per call and isn't perfectly repeatable.

---

## 4. Compare — rules vs AI

```mermaid
flowchart LR
    R["Track 1 tags"]
    A["Track 2 tags"]
    C["Compare job"]
    OUT["Agreement per topic<br/>(where they match / differ)"]

    R --> C
    A --> C
    C --> OUT
```

Run after Tracks 1 and 2. Shows how closely the AI matches the rules so GM can
decide which to trust (or how to blend them).

---

## 5. Track 3 — Sentiment model

```mermaid
flowchart LR
    IN["Sentences"]
    LABEL["Bootstrap labels<br/>(LLM, since we have none)"]
    TRAIN["Fine-tune DistilBERT<br/>save to MLflow"]
    SCORE["Score sentiment<br/>Very Neg .. Very Pos"]

    IN --> LABEL --> TRAIN --> SCORE
```

A trained model GM would own. A simple word-list baseline (VADER) is scored
alongside it for comparison. Trained on bootstrapped labels for now; refine on
real reviewed labels later.

---

## How the tracks relate (summary)

- **Tracks 1 & 2 = the same job (topics), compared.** Pick one or blend.
- **Track 3 = a different job (sentiment), complementary.** Use with the winner.
- Together you get, per sentence: **what the customer is talking about** + **how
  they feel** — which sets up questions like "how do customers feel about points
  redemption specifically?"
