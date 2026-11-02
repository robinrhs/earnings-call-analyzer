# Earnings call brief: Fernhill Automation Inc. (fictional), Q3 FY2026

> Built from a hand-authored fixture that stands in for a model response (no language model was called) and checked automatically against the transcript. Verification shows that a quote exists, not that the interpretation is right. This is a research aid, not investment advice.

- Source: `fernhill_q3_fy2026.txt`
- Model: `fixture (hand-authored)`, replayed from fernhill_q3_fy2026.json (no API call)
- Note: Hand-authored fixture, not a real API response. Written to look like a model reply for the fictional transcript, with four deliberate citation errors that the verifier should catch: a wrong paragraph id, a dash/comma variant, a dropped word and a fabricated figure.
- Verification: 33 quotes checked: 31 verified, 1 corrected (wrong paragraph id fixed), 1 unverified. Unverified items are removed from the brief and listed under *Rejected by verification*. Fuzzy threshold: 0.90.

## Management tone: Constructive `VERIFIED`

Management reported a record quarter above its own range and raised full-year revenue guidance, which supports a positive label. It was also explicit about weaker European orders and the cash cost of tariffs, and it declined to discuss fiscal 2027 margins, so the tone is constructive rather than confident.

- `VERIFIED` P005: "The third quarter was our strongest quarter to date"
- `VERIFIED` P009: "decision cycles are clearly longer, and we are planning conservatively for the region"
- `VERIFIED` P035: "we are not going to give fiscal 2027 guidance today"

## Headline figures

- **Revenue: $412.3 million** (+14% year over year, from $361.7 million), Q3 FY2026
  `VERIFIED` P014: "third quarter revenue was $412.3 million, up 14% from $361.7 million in the prior-year quarter"
- **Annual recurring revenue (ARR): $588 million** (+22% year over year), Q3 FY2026
  `VERIFIED` P006: "Annual recurring revenue from our Fernhill Orchestrate software platform reached $588 million at quarter end, up 22% from a year ago"
- **Gross margin: 47.8%** (46.1% a year ago), Q3 FY2026
  `VERIFIED` P016: "Gross margin expanded to 47.8%, compared with 46.1% a year ago"
- **Adjusted operating margin: 15.2%** (13.9% a year ago), Q3 FY2026
  `CORRECTED P017->P016` P016: "Adjusted operating margin was 15.2%, up from 13.9% in the third quarter of last year"
    Quote found in P016, not in P017; id corrected.
- **Adjusted EPS: $0.74** ($0.61 a year ago), Q3 FY2026
  `VERIFIED` P016: "Adjusted earnings per share were $0.74, compared with $0.61 a year ago"
- **Free cash flow: $38.5 million**, Q3 FY2026
  `VERIFIED` P018: "Free cash flow was $38.5 million in the quarter"
- **Book-to-bill ratio: 1.08**, Q3 FY2026
  `VERIFIED` P007: "We booked $445 million of new orders in the quarter, which translates into a book-to-bill ratio of 1.08"
- **Backlog: $1.21 billion** (record level), Q3 FY2026
  `VERIFIED` P007: "our backlog closed the quarter at a record $1.21 billion"

## Guidance

- **Revenue, FY2026: $1.62 billion to $1.66 billion** (raised; prior: $1.58 billion to $1.63 billion)
  `VERIFIED` P019: "we are raising our fiscal 2026 revenue guidance to a range of $1.62 billion to $1.66 billion, up from our prior range of $1.58 billion to $1.63 billion"
- **Adjusted operating margin, FY2026: approximately 15%** (maintained)
  `VERIFIED` P019: "We continue to expect an adjusted operating margin of approximately 15% for the full year"
- **Free cash flow, FY2026: $120 million to $135 million** (lowered; prior: $140 million to $155 million)
  `VERIFIED` P021: "We are lowering our fiscal 2026 free cash flow guidance to $120 million to $135 million, compared with our previous range of $140 million to $155 million"
- **Revenue, Q4 FY2026: $435 million to $450 million** (initiated)
  `VERIFIED` P020: "For the fourth quarter, we expect revenue in the range of $435 million to $450 million"

## Key risks

- **Slower European orders** (demand): European grocery and apparel customers are delaying investment decisions; orders fell 9% and the revenue impact lags by two to three quarters.
  `VERIFIED` P009: "European orders were down 9% year over year"
- **Tariffs on imported components** (trade / regulation): New tariffs on motion-control components raise costs; management expects to offset most of it only by mid next year.
  `VERIFIED` P010: "New tariffs on imported motion-control components took effect in August"
- **Pre-bought inventory ties up cash** (costs / supply chain): Securing component supply required an inventory build, which is the main reason free cash flow guidance was cut.
  `VERIFIED (normalized)` P010: "We have secured supply for the next two quarters, but we pre-bought inventory to do so — and that ties up cash."
    Transcript reads: "We have secured supply for the next two quarters, but we pre-bought inventory to do so, and that ties up cash."
- **Competitors pricing aggressively on smaller projects** (pricing / competition): Fernhill is not matching lower prices, which may cost some deals near term.
  `VERIFIED` P032: "We are seeing some competitors become more aggressive on price for smaller projects"
- **Customer concentration** (customer concentration): The largest customer is 17% of revenue year to date; the renewal reduces but does not remove the risk.
  `VERIFIED` P040: "We do still regard customer concentration as a risk"

## Analyst questions

### Free cash flow guidance cut - Priya Raman (Halcyon Securities), answer: direct

- Question: Why cut free cash flow guidance by about $20 million while raising revenue, and is it really timing?
  `VERIFIED` P024: "You are raising revenue but cutting free cash flow by about $20 million at the midpoint"
- Answer: About $15 million is pre-bought inventory and about $5 million absorbed tariff cost; the inventory is tied to backlog projects shipping in the first half of fiscal 2027.
  `VERIFIED` P025: "about $15 million is the component inventory we pre-bought and about $5 million is tariff cost we have absorbed"

### Pricing to offset tariffs - Priya Raman (Halcyon Securities), answer: direct

- Question: Does the raised revenue range assume price increases to offset tariffs?
  `VERIFIED` P026: "does the raised revenue range assume any price increases to offset the tariffs?"
- Answer: Only already-announced increases on new orders since September; the benefit builds mainly in fiscal 2027.
  `VERIFIED` P027: "It includes the price increases we have already announced, which took effect on new orders from September"

### European demand - Marcus Oyelaran (Ridgefield Partners), answer: partial

- Question: Is the European slowdown a pause or a shift away from automation, and what is assumed for Q4?
  `VERIFIED` P029: "Is this a temporary pause, or are you seeing customers shift budgets away from automation altogether?"
- Answer: Described as a delay, not a shift; Q4 assumes European orders stay at Q3 levels; management would not call the bottom.
  `VERIFIED (fuzzy 0.98)` P030: "What we hear from customers is that projects are still on their roadmaps, but boards are asking for more certainty"
    Transcript reads: "What we hear from customers is that the projects are still on their roadmaps, but boards are asking for more certainty"

### Fiscal 2027 margins - Elena Sokolova (Brightline Capital Markets), answer: deflected

- Question: Should investors expect pricing pressure and lower margins next year?
  `VERIFIED` P034: "should we expect pricing pressure and a step back in margins next year?"
- Answer: Management declined to give fiscal 2027 guidance before January and pointed to software mix and its margin framework.
  `VERIFIED` P035: "we are not going to give fiscal 2027 guidance today; we will do that in January as usual"

### Largest customer renewal - Tom Achterberg (Northgate Research), answer: direct

- Question: Is the multi-year renewal with the largest customer on track?
  `VERIFIED` P039: "is the multi-year contract renewal still on track for this year?"
- Answer: Signed in October as a five-year agreement; the customer was 17% of revenue year to date.
  `VERIFIED` P040: "I am pleased to say we signed the renewal in October"

### Orchestrate 5 pricing uplift - Kenji Moreau (Ashgrove & Lyle), answer: direct

- Question: What is the price uplift from Orchestrate 5 upgrades and when does it reach ARR?
  `VERIFIED` P042: "How should we think about the price uplift per customer, and when does that show up in annual recurring revenue?"
- Answer: Around 25% subscription uplift for customers taking both modules, flowing into ARR at renewal dates over four to six quarters.
  `VERIFIED` P043: "the upgrade carries an average subscription uplift of around 25% for customers who take both new modules"

## Rejected by verification

These items were returned by the model but their quotes could not be found in the transcript, so they are not part of the brief.

- Headline figures: Net revenue retention (software): 118%
  - Cited P006. Quote not found in the transcript. Closest passage: P017 (similarity 0.61).
