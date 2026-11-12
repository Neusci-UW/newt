// Argus lesson "The GHK equation". Edit it on its simulation page: open the page with ?edit,
// or press Edit lesson in Argus. See web/lesson.js for the format.
ArgusLesson.define({
  "id": "membrane_ghk",
  "title": "The GHK equation",
  "sim": "membrane",
  "questions": [
    {
      "id": "rtf37",
      "type": "num",
      "anchor": { "root": "#ghk-derivation", "path": [["dl", 0]], "text": "membrane potential, gas constant, absolute temperature, Fara" },
      "q": "Work out RT/F at body temperature, 37 °C. Use R = 8.314 J/(mol·K) and F = 96 485 C/mol.",
      "answer": 26.7,
      "tol": 0.3,
      "unit": "mV",
      "placeholder": "25.0",
      "explain": "RT/F = 8.314 × 310.15 / 96 485 ≈ 0.0267 V = 26.7 mV. Check it with the T slider on the calculator."
    },
    {
      "id": "scaleP",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["dl", 0]], "text": "membrane potential, gas constant, absolute temperature, Fara" },
      "q": "If every permeability doubles, what happens to V<sub>m</sub>?",
      "options": [
        { "t": "It moves toward 0 mV" },
        { "t": "Nothing: the factor of 2 cancels between the top and bottom of the fraction", "ok": true },
        { "t": "It doubles" }
      ],
      "explain": "Every term on the top and the bottom of the GHK fraction has one P in it, so scaling them all cancels. That is why only the ratios between ions matter."
    },
    {
      "id": "pump",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["p", 0]], "text": "(1) Steady state, with each ion crossing independently. (2) " },
      "q": "The Na⁺/K⁺ pump moves 3 Na⁺ out for every 2 K⁺ in. Which assumption does it break?",
      "options": [
        { "t": "The constant field" },
        { "t": "That the only currents are passive ions crossing independently", "ok": true },
        { "t": "The partition coefficient at each face" }
      ],
      "explain": "GHK counts only passive fluxes. The pump carries one net positive charge out per cycle, a current of its own, so a real resting potential sits a few mV more negative than GHK predicts."
    },
    {
      "id": "field",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["p", 0]], "text": "(1) Steady state, with each ion crossing independently. (2) " },
      "q": "Which of these would make the field inside the membrane not constant?",
      "options": [
        { "t": "Raising the temperature" },
        { "t": "Raising [K⁺]<sub>o</sub>" },
        { "t": "Charged amino acids lining the channel", "ok": true }
      ],
      "explain": "Fixed charges inside the membrane bend the potential profile, so it no longer falls in a straight line. Temperature and concentrations change the voltage, not the shape of the field."
    },
    {
      "id": "diffdir",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["div", 0]], "text": "" },
      "q": "K⁺ is more concentrated inside. Which way does the diffusion term alone push K⁺?",
      "options": [
        { "t": "Out of the cell", "ok": true },
        { "t": "Into the cell" },
        { "t": "Neither, without a voltage" }
      ],
      "explain": "Concentration falls from x = 0 (inside) to x = d (outside), so dc/dx &lt; 0 and −D·dc/dx &gt; 0. Positive J means outward."
    },
    {
      "id": "jzero",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["div", 0]], "text": "" },
      "q": "Set J<sub>s</sub> = 0 in the Nernst–Planck equation and integrate across the membrane. What do you get?",
      "options": [
        { "t": "Fick’s law" },
        { "t": "The Nernst equation", "ok": true },
        { "t": "Ohm’s law" }
      ],
      "explain": "With no net flux, diffusion and drift balance exactly: dc/c = −(zF/RT)dφ. Integrating gives V<sub>m</sub> = (RT/zF) ln([S]<sub>o</sub>/[S]<sub>i</sub>), the Nernst potential. Step 5 gets there the long way."
    },
    {
      "id": "fieldsize",
      "type": "num",
      "anchor": { "root": "#ghk-derivation", "path": [["div", 1]], "text": "" },
      "q": "With V<sub>m</sub> = −70 mV across a membrane 5 nm thick, how strong is the field? Answer in megavolts per metre.",
      "answer": 14,
      "tol": 0.7,
      "unit": "MV/m",
      "placeholder": "1.0",
      "explain": "70 mV / 5 nm = 0.070 V / 5 × 10⁻⁹ m = 1.4 × 10⁷ V/m = 14 MV/m. That is close to the field at which air breaks down into a spark."
    },
    {
      "id": "ucl",
      "type": "num",
      "anchor": { "root": "#ghk-derivation", "path": [["div", 1]], "text": "" },
      "q": "For Cl⁻ at V<sub>m</sub> = −70 mV and 25 °C (RT/F = 25.7 mV), what is u<sub>s</sub> = z<sub>s</sub>FV<sub>m</sub>/RT?",
      "answer": 2.72,
      "tol": 0.1,
      "unit": "",
      "placeholder": "1.0",
      "explain": "u = (−1)(−70 mV)/(25.7 mV) ≈ +2.72. The two negatives cancel: z is −1 and V<sub>m</sub> is negative."
    },
    {
      "id": "jconst",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["div", 4]], "text": "" },
      "q": "Why is J<sub>s</sub> the same at every depth x?",
      "options": [
        { "t": "The membrane is thin" },
        { "t": "At steady state ions can’t pile up anywhere inside the membrane", "ok": true },
        { "t": "The field is constant" }
      ],
      "explain": "If more ions entered a slice than left it, the concentration there would change with time. Steady state rules that out, so the same flux passes every x."
    },
    {
      "id": "fick",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["div", 4]], "text": "" },
      "q": "With no voltage (u<sub>s</sub> → 0), what does the GHK flux equation become?",
      "options": [
        { "t": "J<sub>s</sub> = P<sub>s</sub>([S]<sub>i</sub> − [S]<sub>o</sub>), Fick’s law", "ok": true },
        { "t": "J<sub>s</sub> = 0" },
        { "t": "J<sub>s</sub> = P<sub>s</sub>[S]<sub>i</sub>" }
      ],
      "explain": "For small u, 1 − e<sup>−u</sup> ≈ u and e<sup>−u</sup> ≈ 1, so the u’s cancel and the flux is permeability times the concentration difference."
    },
    {
      "id": "fluxes",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["p", 8]], "text": "Solving for and multiplying by gives the GHK voltage equatio" },
      "q": "At the GHK resting potential, what is the flux of each individual ion?",
      "options": [
        { "t": "Zero for every ion, so nothing moves" },
        { "t": "Not zero: K⁺ leaks out and Na⁺ leaks in, but the charges they carry cancel", "ok": true },
        { "t": "Zero for the most permeant ion only" }
      ],
      "explain": "GHK only makes the total current zero. Each ion keeps leaking down its gradient, so without a pump the gradients slowly run down. The last questions on this page let you watch that happen."
    },
    {
      "id": "anion",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["p", 8]], "text": "Solving for and multiplying by gives the GHK voltage equatio" },
      "q": "Why does Cl⁻ appear with inside and outside swapped in the GHK equation?",
      "options": [
        { "t": "Cl⁻ is more concentrated outside" },
        { "t": "Its charge is negative, so it carries current the opposite way for the same flux", "ok": true },
        { "t": "Cl⁻ is pumped" }
      ],
      "explain": "A Cl⁻ ion moving in carries the same current as a K⁺ ion moving out. Flipping z from +1 to −1 flips u, which swaps which concentration sits with the e<sup>−v</sup> factor."
    },
    {
      "id": "tenfold",
      "type": "predict",
      "anchor": { "root": "#ghk-derivation", "path": [["p", 9]], "text": "If only K⁺ crosses, the other terms vanish and . With severa" },
      "q": "Only K⁺ is permeable, with [K⁺]<sub>i</sub> = 140 mM, at 25 °C. If [K⁺]<sub>o</sub> rises from 5 to 50 mM, by how many mV does V<sub>m</sub> change?",
      "tol": 6,
      "unit": "mV",
      "placeholder": "+30",
      "setup": { "calcModel": "ghk", "gPK": 1, "gPNa": 0, "gPCl": 0, "gKi": 140, "gKo": 5, "gT": 25 },
      "task": "The calculator is set to K⁺ only, with [K⁺]<sub>o</sub> = 5 mM. Drag [K⁺]<sub>o</sub> to 50 mM.",
      "done": { "key": "gKo", "min": 48, "max": 52 },
      "measure": { "key": "calcVm", "relative": true },
      "goTo": "calc",
      "explain": "A tenfold change in [K⁺]<sub>o</sub> shifts E<sub>K</sub> by (RT/F)·ln 10 ≈ 59 mV at 25 °C, whatever the starting values."
    },
    {
      "id": "channels",
      "type": "mc",
      "anchor": { "root": "#ghk-derivation", "path": [["p", 13]], "text": "Inside each channel the potential falls linearly across the " },
      "q": "In the simulation, what sets the permeability of each ion?",
      "options": [
        { "t": "A number in the GHK equation" },
        { "t": "How many channels there are for it", "ok": true },
        { "t": "How fast its ions move" }
      ],
      "explain": "Every channel is always open, so more channels for an ion means more crossings for the same gradient. The GHK formula is only used to draw the reference line."
    },
    {
      "id": "naup",
      "type": "predict",
      "anchor": { "region": "calc" },
      "q": "Start from the mammal values: P<sub>K</sub> : P<sub>Na</sub> : P<sub>Cl</sub> = 1 : 0.04 : 0.45, which gives V<sub>m</sub> ≈ −67 mV. Raise P<sub>Na</sub> tenfold to 0.4. What is the new V<sub>m</sub>?",
      "tol": 8,
      "unit": "mV",
      "placeholder": "−50",
      "setup": { "calcModel": "ghk", "gPK": 1, "gPNa": 0.04, "gPCl": 0.45, "gKi": 140, "gKo": 5, "gNai": 14, "gNao": 143.5, "gCli": 10.5, "gClo": 148.5, "gT": 25 },
      "task": "The calculator is set to the mammal values. Drag P for Na⁺ to 0.40.",
      "done": { "key": "gPNa", "min": 0.37, "max": 0.43 },
      "measure": { "key": "calcVm", "relative": false },
      "goTo": "calc",
      "explain": "V<sub>m</sub> moves toward E<sub>Na</sub> (about +60 mV) but stops near −30 mV, because K⁺ and Cl⁻ still pull toward their own Nernst potentials. GHK is a compromise weighted by permeability."
    },
    {
      "id": "zero",
      "type": "goal",
      "anchor": { "region": "calc" },
      "q": "Starting from the mammal values, change only P<sub>Na</sub> until V<sub>m</sub> is 0 mV (within 1 mV). What ratio P<sub>Na</sub>/P<sub>K</sub> does it take?",
      "setup": { "calcModel": "ghk", "gPK": 1, "gPNa": 0.04, "gPCl": 0.45, "gKi": 140, "gKo": 5, "gNai": 14, "gNao": 143.5, "gCli": 10.5, "gClo": 148.5, "gT": 25 },
      "task": "Drag P for Na⁺ until V<sub>m</sub> reads 0.",
      "done": { "key": "calcVm", "min": -1, "max": 1 },
      "report": ["gPNa", "gPK"],
      "goTo": "calc",
      "explain": "Na⁺ has to be about as permeant as K⁺ and Cl⁻ together before it cancels them. At the peak of an action potential P<sub>Na</sub> rises to about 20 times P<sub>K</sub>, which is how V<sub>m</sub> overshoots 0."
    },
    {
      "id": "simmatch",
      "type": "mc",
      "anchor": { "region": "calc" },
      "q": "Press “Send to simulation” and let it run for a while. Does the simulated voltage land exactly on the dotted GHK line?",
      "options": [
        { "t": "Yes, exactly" },
        { "t": "Close, but a few mV off", "ok": true },
        { "t": "No, it ends up near E<sub>K</sub>" }
      ],
      "explain": "The simulation is 2D: ions crowd a little at busy channel mouths and the charge layers carry a few mV, so it lands close to GHK but not on it."
    },
    {
      "id": "rundown",
      "type": "mc",
      "anchor": { "region": "calc" },
      "q": "In the simulation, uncheck both “Hold … concentrations fixed” boxes and leave the pump off. What happens to V<sub>m</sub> over time?",
      "options": [
        { "t": "It stays where it is" },
        { "t": "It drifts toward 0 mV as the gradients run down", "ok": true },
        { "t": "It becomes more negative" }
      ],
      "explain": "Each ion keeps leaking (step 4), so the concentrations slowly even out and every Nernst potential heads toward 0. Turn on the Na⁺/K⁺ pump to hold the gradients up."
    }
  ],
  "regions": []
});
