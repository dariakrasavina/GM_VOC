# GM VOC POC — Architecture

Simple diagrams of how the pieces fit together. They render on GitHub and in any
Mermaid viewer (e.g. https://mermaid.live).

Terms (as XM Discover uses them): a **category model** holds **categories**;
**classification** is the process of assigning sentences to those categories;
**topic modeling** discovers new themes; **sentiment** is a separate layer.

Two tracks both do **classification** and are **compared** so GM can pick the
best. **Topic modeling** is exploratory. The **sentiment model** is used
**alongside** the winner.

---

## 1. The big picture

```mermaid
flowchart LR
    DATA["Call transcripts<br/>(one row per sentence)"]

    subgraph CLS["Classification — compare & pick"]
        RE["Rule engine"]
        AI["ai_classify"]
    end

    TM["Topic modeling<br/>(find new themes)"]
    SENT["Sentiment model"]
    OUT["Category + Sentiment<br/>per sentence"]

    DATA --> RE
    DATA --> AI
    DATA --> TM
    DATA --> SENT
    RE --> CMP{{"Compare"}}
    AI --> CMP
    CMP --> OUT
    SENT --> OUT
```

---

## 2. Classification — rule engine

```mermaid
flowchart LR
    CM["category_model.json<br/>(GM's categories + rules)"]
    IN["Sentences"]
    ENGINE["Rule engine<br/>keyword / phrase / boolean match"]
    TAGS["Categories per sentence<br/>+ which words matched"]

    CM --> ENGINE
    IN --> ENGINE --> TAGS
```

Deterministic and explainable. The category rules come from GM's workbook; the
engine runs the same way locally and on Databricks. **Multi-label** (a sentence
can get several categories) with **hierarchy roll-up** — a hit on a leaf like
"Points - Redeem" also counts toward its parents (Points → Rewards → Loyalty),
exactly like an XM Discover category model.

---

## 3. Classification — ai_classify (+ topic modeling)

```mermaid
flowchart LR
    IN["Sentences"]
    CLASSIFY["LLM (ai_query)<br/>all applicable categories"]
    DISCOVER["topic modeling<br/>embeddings + clustering"]
    TAGS["AI category tags<br/>(multi-label + roll-up)"]
    THEMES["New themes"]

    IN --> CLASSIFY --> TAGS
    IN --> DISCOVER --> THEMES
```

The LLM returns **all** categories that apply to a sentence (multi-label), and
those roll up to parent categories — the same shape as the rule engine, so the
two are directly comparable. Topic modeling groups sentences into themes nobody
defined. Flexible, but costs per call and isn't perfectly repeatable.

---

## 4. Compare — rule engine vs ai_classify

```mermaid
flowchart LR
    R["Rule-engine tags"]
    A["ai_classify tags"]
    C["Compare job"]
    OUT["Agreement per category<br/>(where they match / differ)"]

    R --> C
    A --> C
    C --> OUT
```

Run after both classifiers. Shows how closely ai_classify matches the rules so GM
can decide which to trust (or how to blend them).

---

## 5. Sentiment model

```mermaid
flowchart LR
    IN["Sentences"]
    LABEL["Bootstrap labels<br/>(LLM, since we have none)"]
    TRAIN["Fine-tune DistilBERT<br/>save to MLflow"]
    SCORE["Score sentiment<br/>Very Neg .. Very Pos"]

    IN --> LABEL --> TRAIN --> SCORE
```

A trained model GM would own. A simple word-list baseline (VADER) is scored
alongside it. Trained on bootstrapped labels for now; refine on real reviewed
labels later.

---

## How the tracks relate (summary)

- **Rule engine & ai_classify = the same job (classification), compared.** Pick one or blend.
- **Topic modeling = discovery**, standalone (no predefined categories to compare against).
- **Sentiment model = a different layer**, complementary. Use with the winner.
- Together you get, per sentence: **which category** + **how they feel** — which
  sets up questions like "how do customers feel about points redemption?"
