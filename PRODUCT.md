# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: evaluators at optical retail chains.** Concretely, the Erroca team (Super-Pharm Group), assessing whether to license Lenses for their own e-commerce site. They arrive at a demo, not a store. Their job is to understand what the AI actually does, judge whether the output is convincing enough to put in front of their own customers, and picture it running on their own inventory.

**Secondary: the people the evaluator forwards it to.** E-commerce, merchandising, and IT/compliance stakeholders, each probing a different question — does the try-on look real, does it work on our catalog, what happens to a customer's face photo.

**There is no shopper user.** Nothing on this site is transactional. The `/storefront` route is an *example* of the capability embedded in an e-commerce page; its cart, checkout, and product pages do not exist and are not meant to.

## Product Purpose

Lenses demonstrates four AI eyewear capabilities running against a real retail catalog, so a retailer can decide whether to license them.

Success is not a completed purchase. Success is an evaluator who (a) understands what each capability does, (b) believes the output quality is shippable to their customers, and (c) can see how it drops into their existing site.

## Positioning

The mechanism a neighbouring product could not truthfully copy: **one shared structured tag vocabulary spans both the face analysis and the product catalog.** The face-analysis prompt is constrained to emit `recommended_tags` using literally the same enum values that the catalog rows are tagged with, so recommendation and inventory match on identical structured attributes.

The consequence: ranking is **local, offline, and deterministic** — no embeddings, no vector database, no similarity search, no per-query API cost. Copying this requires re-tagging an entire catalog against a shared schema, not calling an API.

The ranking engine itself carries non-obvious work: weighted fields, five ordered filter stages with graceful relaxation when a stage would shrink the pool below viability, ~70 colour aliases normalised on both query and product sides, material-family folding, a non-relaxable sport-isolation filter, and visual-signature dedup so near-identical frames don't fill all three result slots.

## Operating Context

- **Evaluation, not commerce.** The site is browsed in a meeting, screen-shared, or forwarded as a link. It is often seen once, briefly, by someone who did not build it.
- **Deployment model:** a self-hosted container on the client's own infrastructure, running on the client's own Google Gemini API key. Lenses is licensed, not hosted. Compute is billed by the client directly to Google.
- **Data posture:** the client is the data controller. Lenses does not store customer images; uploads are written to a temp file and unlinked after processing.
- **A face photo is the entry cost to every capability.** Three of the four require the visitor to upload a picture of themselves before anything happens. This is the single highest-friction moment in the product and it is unavoidable.

## Capabilities and Constraints

**Four capabilities, each separately reachable:**

| Capability | Input | Output |
|---|---|---|
| Smart Fit | one portrait | face analysis → 3 photorealistic try-ons of the visitor in 3 real frames, + a face profile |
| Free Search | portrait + attribute preferences | ranked match → 3 try-ons with fit / style / colour subscores |
| Lens Recolor | photo of the visitor already wearing glasses + exactly 3 of 16 named colours | 3 AI-recoloured versions |
| Storefront | a browsable example store | the above, embedded as a layer in a retail page |

**Latency is the defining constraint.** Generation takes 20–50+ seconds. Tuned expectations in code: Smart Fit ~30s; Free Search, single try-on, and recolor ~20s each. Polling runs every 2s. Results stream progressively — the first of three arrives before the other two.

**Technical constraints:** stdlib `http.server`, no web framework, no build step, no bundler, no client framework. Sessions are an in-process dict with no persistence and no eviction; the app is single-process and cannot be horizontally scaled as written. Input images are downscaled to 2048px on the long side; output aspect ratio snaps to one of eight presets. Every AI call goes to Google Gemini and nowhere else — `gemini-2.5-flash` for face analysis, `gemini-3-pro-image-preview` for all image generation.

**Terminology is fixed and shared with the backend.** Frame shapes, materials, colours, lens types, lens colours, lens shapes, sizes, aesthetics, and occasions all come from a defined tag schema; face analysis emits face shape, a nine-field face summary, and three paragraphs of face insights. The UI must use the product's own words, not invented synonyms.

**Undecided:** whether the app is ever deployed beyond localhost; whether the Erroca engagement proceeds.

## Brand Commitments

- The product is named **Lenses**.
- **The demo storefront stays fictional.** It is an invented store, not Erroca's. This keeps the demo reusable for any prospect and keeps a real prospect's brand out of the repository.
- **English only.** No Hebrew, no RTL, despite the commercial proposal being in Hebrew and prices being in ILS.
- Prices display in **ILS (₪)**, because that is the real currency of the real catalog.
- Voice, where the product speaks about a face: **"a master optician sharing fascinating observations"** — specific, curious, second person. Already specified in the analysis prompt and already generated; it is the product's established register.

## Evidence on Hand

**Real and usable:**
- **147 real products** scraped from erroca.co.il, at `lenses/catalog/catalog.json` — real brands (Ray-Ban 50, Prada 26, Versace 19, Miu Miu 14, Vogue Eyewear 8, Emporio Armani 7, Jimmy Choo 5, David Beckham 4, Tom Ford 3, Dolce & Gabbana 3, Oakley 3, Saint Laurent 2, Balenciaga 1), real ILS prices spanning ₪399–1999.
- **148 real product photographs** at `lenses/catalog/images/` (4.6 MB, jpg + webp pairs).
- **Three paragraphs of face insights per analysis**, generated, cached, returned by the API at `/api/status/<id>` — and currently rendered nowhere in the UI. The single largest unused asset in the product.
- **A nine-field face summary** including hair, eye, and skin hex values sampled from the visitor's own photo.
- **Real match reasoning** — every result carries an overall score plus fit / style / colour subscores the UI does not explain.
- The commercial proposal at the repository root (Hebrew, for Erroca / Super-Pharm).

**Absences that must not be fabricated:** there are no customers, no testimonials, no case studies, no press, no benchmarks, no usage statistics, and no analytics of any kind. No deployment beyond local exists. Nothing may claim otherwise.

## Product Principles

1. **Show the work, don't assert it.** This is a demonstration. Every claim about the AI should be visible as output — a rendered face, a named reason, a real product — rather than stated as a feature bullet.
2. **The four capabilities stay four.** They are the product surface area being evaluated; collapsing them into fewer entry points would hide what is being sold. Sequence them as an argument, don't flatten them into a menu of equals.
3. **Real data over placeholder data, always.** Real brands, real photographs, real ILS prices. The catalog being genuinely theirs is the difference between "this could work" and "this works on your inventory." Never anonymize the proof.
4. **The wait is content, not friction.** Twenty to fifty seconds of unexplained delay reads as broken; the same seconds narrated with the AI's actual findings read as difficult work being done well. Disclose the duration honestly up front and fill it with real output.
5. **Answer the compliance question before it is asked.** A face-photo product sold to a pharmacy group must state its data posture where the upload happens — client is data controller, images are not stored. It is currently in the proposal and nowhere in the interface.

## Accessibility & Inclusion

No client-specific standard has been established. The product-specific requirements that do exist:

- The interface is browsed live in meetings and on projectors, so contrast and type size carry more weight than usual. WCAG AA (4.5:1 body, 3:1 large text) is the floor.
- Long asynchronous operations must be announced to assistive technology, not only shown. Live regions already exist on the storefront and should be the norm rather than the exception.
- Motion must respect `prefers-reduced-motion` across every surface; today only one stylesheet does.
- Face analysis emits a mandatory binary `gender` field ("men" or "women") because it selects which inventory partition is searched. This is a catalog-structure constraint, not a claim about the visitor. The UI should not present it as an identity judgement.
