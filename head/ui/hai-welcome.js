/* HAI welcome — standalone, dependency-free release greeting. */
(() => {
  'use strict';
  const RELEASES = {
    '1.8': { label: 'Preview', intro: 'You met HAI. Then we kept building.\nLet’s show you around.' },
    '1.9': { label: 'Welcome', intro: 'You met HAI. Then we kept building.\nLet’s show you around.' }
  };
  // This is a cumulative catch-up, not a claim that every item first shipped in 1.9.
  const GROUPS = [
  {
    "title": "Your conversations, opened up",
    "intro": "A more useful place to chat, follow the work, and change direction."
  },
  {
    "title": "HAI grew a creative side",
    "intro": "Images, your own photos, and a more personal kind of picture."
  },
  {
    "title": "More control. More continuity.",
    "intro": "Building on the workspaces and long thinking you already had."
  },
  {
    "title": "The lab behind the chat",
    "intro": "New models, measured decisions, and a much more coordinated engine."
  }
];
  const FOUNDATIONS = [
  "The original longer Think hard workflow, plus the long-budget Absurd runs.",
  "General, Nursing, and Law hats, with the nursing study and evidence workflows.",
  "Personal memory, document uploads, searchable private libraries, and nursing writing-voice support.",
  "Coding workspaces with file tools, tests, local Git checkpoints, and isolated worktrees.",
  "Goal planning, saved plans, and dependency graphs.",
  "The original model choices, lab operations tools, and permission boundaries."
];
  const NOTES = [
  {
    "group": 0,
    "name": "Conversations you can actually organize",
    "short": "A sidebar, search, names, archives, and exports.",
    "details": [
      "Find your conversations in the sidebar, rename them to something useful, search your chats, archive the ones you are finished with, and export a conversation to keep a copy.",
      "New chat starts another conversation while saved long-term notes remain separate. You can also pin useful parts of a conversation and choose how much of HAI’s work is shown."
    ]
  },
  {
    "group": 0,
    "name": "Branch from any answer",
    "short": "Try another direction without losing the one you have.",
    "details": [
      "Every answer has a ⑂ Branch button underneath it. It starts a new chat that carries the whole conversation up to that answer, so you can push an idea a different way, ask a what-if, or start over from a good point.",
      "The original chat stays exactly as it was, and the branch appears in your chat list like any other conversation. Branch as many times as you like and compare where each one goes."
    ],
    "example": "Branch from the outline you liked, then ask: “Now make it twice as funny.”"
  },
  {
    "group": 0,
    "name": "See the work as it happens",
    "short": "Live transcripts and order cards give the wait some context.",
    "details": [
      "Your message is recorded when the run is accepted, and the live transcript shows progress while HAI works. Orders started by a conversation can show their own cards inside it.",
      "Open devices can follow the same conversation’s live events. Normal, Thinking, Verbose, and Summary views let you choose between the answer, the reasoning journal, and the tool trail."
    ]
  },
  {
    "group": 0,
    "name": "You can talk to it while it’s busy",
    "short": "A running task does not have to mean a locked composer.",
    "details": [
      "Send a correction, extra requirement, or clarification while a run is underway. HAI picks it up at the next natural step of its work loop.",
      "The follow-up is shown with the run and saved with the answer. Delivery happens between model steps, so it is not necessarily an instant interruption."
    ],
    "example": "One more thing: keep the explanation short and include a worked example."
  },
  {
    "group": 0,
    "name": "A better fit for your phone",
    "short": "The sidebar and conversation stay inside the screen.",
    "details": [
      "Phone layouts gained a collapsible chat list, with a close button and backdrop so the sidebar cannot trap you over the conversation. Choosing a chat closes the list on narrow screens.",
      "Long code lines and tables scroll inside their own areas, and images shrink to the conversation width. The composer, smaller-screen controls, and account picker also received layout fixes."
    ]
  },
  {
    "group": 1,
    "name": "Pictures belong in the conversation",
    "short": "Ask for an image where you’re already chatting.",
    "details": [
      "Describe an illustration, scene, poster, or wallpaper in your normal message. You do not need to move into a separate image conversation.",
      "Choose an image preference alongside your text-model preference. The Flux.2 option pins that renderer; loading cards use the real render order’s information.",
      "The existing image tools also support shapes, wallpaper sizes, style presets, and multiple variations, subject to the chosen renderer and your account’s image allowance."
    ],
    "example": "Make me a watercolor wallpaper of a tiny house under a starry sky."
  },
  {
    "group": 1,
    "name": "Your photos can be a starting point",
    "short": "Upload a picture and ask for a restyle.",
    "details": [
      "Use your latest uploaded photo as the source for a sketch, watercolor, oil-painting, anime, or photo-style transformation. The restyle path can use the source’s edges to help preserve its composition.",
      "Photo restyling currently uses the Auto image path. The explicitly selected Flux.2 path supports new images and saved identity models; it reports that uploaded-image editing is unavailable rather than silently changing your renderer."
    ],
    "example": "Turn my latest photo into a watercolor painting."
  },
  {
    "group": 1,
    "name": "“Me” can actually mean you",
    "short": "Personal portraits can use a saved model for your account.",
    "details": [
      "When a usable identity model has been set up for your account, a request about “me” or “myself” can use that model in the ordinary conversation.",
      "The identity library also lists the people your account is permitted to render, along with model availability. A missing or offline model is reported; HAI does not quietly substitute a generic lookalike."
    ],
    "example": "Make a portrait of me in a cozy little bookshop."
  },
  {
    "group": 1,
    "name": "A few more playful experiments",
    "short": "Family portraits and imagined siblings joined the image toolbox.",
    "details": [
      "The image tools include family scenes and an imagine-children workflow using two uploaded parent photos. Different samples can produce different imagined siblings.",
      "These are creative, entertainment-grade images. The feature uses facial references and inheritance-inspired transformations; it is not a prediction of real children’s genetics. Availability depends on the required image assets and account access."
    ],
    "example": "Using these two photos, imagine two different siblings."
  },
  {
    "group": 2,
    "name": "The controls grew beyond the old model ladder",
    "short": "Thinking effort, work mode, and your preferred model have clearer jobs.",
    "details": [
      "The original HAI already had model choices and Think hard. The newer surface separates a model’s own thinking effort from the kind of work and time budget you give it. Auto can choose, or you can set your own preference.",
      "Your model and effort pins now follow your account between devices. A preference set on your phone can be picked up on your desktop.",
      "The 27B choice is now available to every user, including in Nursing. If that explicit choice is busy, it keeps a visible queue with an estimated wait and a reroute offer; it does not quietly turn into another model."
    ]
  },
  {
    "group": 2,
    "name": "Absurd became Background",
    "short": "The long-run idea stayed. The surrounding experience grew.",
    "details": [
      "Long time budgets were part of the HAI you launched. The newer interface calls this Background and puts it alongside the work controls instead of making it a separate personality of the assistant.",
      "The surrounding agent surface adds live work visibility and the ability to send a message while it is working. Supported runs can be parked and resumed, rather than treating every service restart as the end of the attempt.",
      "The time budget is still a ceiling, not a target to fill. Results report when a budget, step limit, or idle condition stops the work. Completion delivery depends on the notification path configured for that run."
    ]
  },
  {
    "group": 2,
    "name": "More of the run can survive an interruption",
    "short": "The old workspace checkpoints grew into a more continuous agent.",
    "details": [
      "Coding checkpoints were already there in the original HAI. The newer work loop also saves the running agent’s state at turns and tool steps, with resume and branch controls for continuing from a saved point.",
      "Deployments can park supported work and resume it afterward. This includes recovery work on the overnight driver, so a planned restart does not have to discard the item it was working on.",
      "Unverified coding work is preserved in its own marked section with a restorable checkpoint. A stopped or unfinished attempt should leave something you can inspect and pick back up."
    ]
  },
  {
    "group": 2,
    "name": "A stronger meaning of “done”",
    "short": "The workspaces existed. The finish checks got stricter.",
    "details": [
      "HAI could already read and edit code in workspaces, run tests, and create Git checkpoints. The newer finish gate focuses on whether a coding job’s CURRENT edits have actually passed its tests.",
      "An edit after a green test run closes the gate again. A job that cannot clear the finish requirements is labeled unverified, rather than being presented as a verified completion.",
      "The reviewer also gained the edits, test results, later tool steps, and reference material needed to assess claims about the work. Self-engineering and deployment keep their separate lab-admin approval gates."
    ]
  },
  {
    "group": 2,
    "name": "See why an answer was held back",
    "short": "A reviewer’s concerns do not have to become a disappearing draft.",
    "details": [
      "When Think hard withholds a draft, the response can expose the draft and the unresolved findings so you can inspect the concern rather than only seeing a refusal.",
      "The admin override workflow can attach a reason as a comment or ask HAI to reconsider and resubmit using that reason. The choice and rationale are recorded. This is separate from ordinary friends’ access to model and thinking controls."
    ]
  },
  {
    "group": 3,
    "name": "New local models, chosen by measurement",
    "short": "The lab learned a lot more than “small is fast, big is clever.”",
    "details": [
      "The model lineup grew through comparative evaluations and larger-context experiments. The lab’s measured menu records quality, time, startup cost, and energy use for the options the hardware can actually serve.",
      "Auto now uses request triage and that menu for placement, rather than only the original hand-written model ladder. Quick gives speed more weight; Think hard leans more heavily toward quality.",
      "McCoy helps assess requests, with uncertain cases sent to the lead by rule. More McCoy work, calibration, and the feedback that will shape 2.0 remain ongoing."
    ]
  },
  {
    "group": 3,
    "name": "Less reloading, more useful waiting",
    "short": "Faster starts and clearer queues got their own attention.",
    "details": [
      "Speed measurements now include the time until the first visible output. Recently used conversations can reuse saved context, reducing unnecessary rereads when that context stays available.",
      "Auto can try another eligible placement when its first choice cannot accept work. An explicit model pin keeps its queue. The UI can tell you when a model is waking, loading, or waiting for a slot.",
      "Energy prices also enter Auto’s decisions. At exceptionally high prices it may wait and explain why; choosing a model explicitly still runs."
    ]
  },
  {
    "group": 3,
    "name": "The lab became a more coordinated workshop",
    "short": "Training, experiments, images, and chat have more shared machinery.",
    "details": [
      "The GPU machines and the tablet Fellowship grew a common way to declare capabilities, accept queued orders, report progress, and account for work. More chat and creative work now travel through those tracked factory orders.",
      "For lab-admin accounts, training jobs, dataset work, model evaluations, and library-maintenance jobs gained their own tools and visible status. These are additions to the lab behind HAI, not new admin permissions for friends.",
      "The work board, live order cards, and factory status make it easier to see what is running or waiting. Camera event lookup and recorded-footage scans also became part of the lab’s tool surface."
    ]
  }
];
  let active = null;
  const escapeHTML = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const css = `
    :host { all:initial; color-scheme:dark; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    *,*::before,*::after { box-sizing:border-box; }
    dialog { --ink:#f5f2ff; --muted:#a6abc1; --lilac:#cabdff; position:fixed; inset:0; margin:0; padding:0; width:100%; max-width:none; height:100%; max-height:none; border:0; background:#0c101b; color:var(--ink); overflow:auto; overscroll-behavior:contain; }
    dialog::backdrop { background:#0c101b; }
    button { font:inherit; cursor:pointer; -webkit-tap-highlight-color:transparent; }
    button:focus-visible,summary:focus-visible { outline:2px solid #cabbff; outline-offset:6px; }
    button:disabled { cursor:default; }
    [hidden] { display:none !important; }
    .canvas { isolation:isolate; position:relative; min-height:100%; display:flex; flex-direction:column; overflow:hidden; }
    .atmosphere { pointer-events:none; position:absolute; inset:0; z-index:-1; overflow:hidden; background:radial-gradient(ellipse at 48% 45%,#17213d70,transparent 62%); }
    .halo { position:absolute; width:min(85vw,850px); height:min(85vw,850px); border-radius:50%; filter:blur(90px); opacity:.18; }
    .halo.a { top:4%; left:3%; background:radial-gradient(ellipse,#8265c5,transparent 67%); }
    .halo.b { right:-22%; top:23%; background:radial-gradient(ellipse,#3f9f9e,transparent 65%); opacity:.12; }
    .halo.c { bottom:-55%; left:5%; background:radial-gradient(ellipse,#af658f,transparent 68%); opacity:.14; }
    .topbar { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:20px; padding:30px clamp(22px,4.5vw,66px); }
    .brand { display:flex; align-items:center; gap:12px; font-weight:650; letter-spacing:.16em; font-size:16px; }
    .brand i { height:20px; width:20px; display:block; border:1.5px solid #d2c7f8; border-radius:7px 7px 10px 10px; transform:rotate(-8deg); position:relative; }
    .brand i::before,.brand i::after { content:''; height:2px; width:2px; border-radius:50%; background:#d2c7f8; position:absolute; top:7px; }
    .brand i::before { left:5px; } .brand i::after { right:5px; }
    .toptools { display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
    .preview-tools { display:flex; align-items:center; gap:12px; }
    .preview-label { color:var(--muted); font-size:11px; letter-spacing:.1em; text-transform:uppercase; }
    .versions { display:flex; border:1px solid #ffffff18; border-radius:100px; padding:3px; }
    .versions button { background:none; border:0; color:var(--muted); padding:7px 14px; font-size:12px; border-radius:100px; }
    .versions button[aria-pressed=true] { color:#fff; background:#ffffff16; }
    .quiet { padding:8px 0; border:0; color:var(--muted); font-size:12px; background:none; }
    .quiet:hover { color:var(--ink); }
    .welcome { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; padding:20px 24px 46px; min-height:520px; }
    .handwriting { width:min(560px,100%); margin:-6px 0 8px; overflow:visible; }
    .handwriting .stroke { fill:none; stroke:url(#hai-release-gradient); stroke-width:8; stroke-linecap:round; stroke-linejoin:round; stroke-dasharray:none; stroke-dashoffset:0; }
    .handwriting .glimmer { animation:glimmer .5s ease-out 3.05s both; transform-origin:477px 43px; }
    .welcome h1 { font-size:clamp(23px,3vw,28px); }
    .kicker { font-size:11px; font-weight:550; color:var(--lilac); letter-spacing:.19em; text-transform:uppercase; margin:10px 0 15px; }
    h1 { margin:0; font-size:clamp(27px,4.4vw,42px); line-height:1.2; font-weight:450; letter-spacing:-.045em; }
    .intro { font-size:15px; color:var(--muted); line-height:1.8; margin:16px 0 0; max-width:470px; white-space:pre-line; }
    .highlights { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:22px; max-width:720px; width:100%; margin:28px auto 0; text-align:left; }
    .highlight { border-top:1px solid #c6b2eb35; padding-top:10px; }
    .highlight strong { display:block; font-size:13px; font-weight:550; line-height:1.5; }
    .highlight span { display:block; color:var(--muted); font-size:12px; line-height:1.7; margin-top:4px; }
    .arrival { animation:arrive .8s ease 1.2s both; }
    .welcome .arrival { width:100%; max-width:720px; }
    .welcome .intro { margin-left:auto; margin-right:auto; }
    .actions { display:flex; align-items:center; flex-direction:column; gap:10px; margin:26px 0 0; }
    .primary { color:#181329; background:#ede6ff; border:1px solid #ffffff30; border-radius:100px; padding:15px 28px; font-size:14px; font-weight:600; display:inline-flex; align-items:center; gap:25px; box-shadow:0 3px 24px #b29ae50c; transition:background .2s,transform .2s; }
    .primary:hover { background:#fff; transform:translateY(-1px); }
    .text-button { border:0; background:none; padding:9px 12px; color:#c0bfd2; font-size:12px; }
    .text-button:hover { color:#fff; }
    .footnote { text-align:center; color:#85899e; font-size:10px; letter-spacing:.16em; text-transform:uppercase; padding:14px 24px 28px; }
    .love-note { display:flex; align-items:center; justify-content:center; gap:8px; color:#c4b5db; font-size:14px; font-style:italic; letter-spacing:.01em; transform:rotate(-5deg); margin:23px 0 0; }
    .love-note svg { width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.5; stroke-linecap:round; }
    .notes { width:min(100%,790px); margin:20px auto 0; padding:0 clamp(22px,5vw,60px) 46px; animation:arrive .35s ease both; }
    .notes .kicker { margin-top:12px; }
    .notes h1 { font-size:clamp(30px,5vw,44px); }
    .notes .intro { max-width:550px; margin-bottom:20px; }
    .baseline { padding:10px 14px; margin:0 0 12px; border:1px solid #ffffff1a; border-radius:14px; background:#ffffff03; }
    .baseline summary { display:flex; align-items:center; justify-content:space-between; padding:0; gap:8px 14px; color:#ddd3ed; font-size:13px; }
    .baseline summary::after { content:'+'; font-size:18px; }
    .baseline[open] summary::after { content:'−'; }
    .baseline-tag { color:#a49ab6; font-size:10px; }
    .baseline div { margin-top:14px; }
    .baseline p,.baseline li { font-size:12px; color:#b3adc5; line-height:1.85; }
    .baseline ul { margin:10px 0 0; padding-left:19px; }
    .chapter-nav { display:flex; flex-wrap:wrap; gap:4px 14px; margin:0; padding:0 0 12px; border-bottom:1px solid #ffffff18; }
    .chapter-nav button { background:none; border:0; padding:6px 0; color:#cfc0ee; font-size:12px; }
    .reading-tools { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:10px 0 20px; border-bottom:1px solid #ffffff18; }
    .reading-tools p { font-size:12px; color:var(--muted); line-height:1.7; margin:0; }
    .chapter { margin-top:28px; scroll-margin-top:24px; }
    .chapter-label { color:#b8a6d6; text-transform:uppercase; font-size:10px; letter-spacing:.17em; margin:0 0 9px; }
    .chapter h2 { font-size:25px; font-weight:450; letter-spacing:-.035em; margin:0; line-height:1.3; }
    .chapter-intro { color:var(--muted); font-size:13px; line-height:1.8; margin:10px 0 12px; }
    .note { border-bottom:1px solid #ffffff12; }
    summary { list-style:none; cursor:pointer; display:grid; grid-template-columns:30px 1fr 16px; align-items:start; gap:12px; padding:20px 0; }
    summary::-webkit-details-marker { display:none; }
    .note-number { color:#baabd6; font-size:11px; padding-top:4px; font-variant-numeric:tabular-nums; }
    .note-name { display:block; font-size:16px; font-weight:500; line-height:1.5; }
    .note-short { display:block; font-size:13px; color:var(--muted); line-height:1.7; margin-top:4px; }
    .plus { color:#afa2c7; font-size:19px; font-weight:300; transition:transform .2s; }
    details[open] .plus { transform:rotate(45deg); }
    .note-details { padding:0 26px 18px 42px; }
    .note-details p { color:#b9bed2; font-size:13px; line-height:1.85; margin:0 0 12px; }
    .note-details .example { border-left:2px solid #a58fc354; padding:2px 0 2px 14px; color:#dbcee9; font-size:13px; font-style:italic; margin:18px 0 6px; }
    .example span { display:block; color:#a7a0ba; font-size:10px; font-style:normal; text-transform:uppercase; letter-spacing:.1em; margin-bottom:5px; }
    .notes-footer { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-top:30px; flex-wrap:wrap; }
    .thanks { color:#a5a0b8; font-size:12px; line-height:1.7; max-width:240px; }
    .brief { width:min(100%,860px); margin:8px auto 0; padding:0 clamp(22px,5vw,60px) 35px; }
    .brief h1 { font-size:clamp(29px,4.6vw,41px); }
    .brief-intro { color:#aeb4c9; font-size:14px; line-height:1.8; margin:14px 0 24px; }
    .brief-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 30px; }
    .brief-item { padding:19px 0; border-top:1px solid #ffffff15; }
    .brief-item:last-child { grid-column:1/-1; }
    .brief-item h2 { font-size:17px; font-weight:500; letter-spacing:-.02em; line-height:1.5; margin:0 0 8px; }
    .brief-item p { color:#aeb5ca; font-size:13px; line-height:1.85; margin:0; }
    .brief-actions { display:flex; flex-wrap:wrap; align-items:center; gap:14px 23px; margin:25px 0 8px; }
    .brief-foot { color:#9395ab; font-size:11px; line-height:1.8; margin:20px 0 0; }
    .outro { flex:1; text-align:center; display:flex; align-items:center; justify-content:center; flex-direction:column; padding:70px 24px; gap:20px; }
    .outro p { color:var(--muted); font-size:14px; line-height:1.8; margin:0; }
    .outro h1 { margin:0; }
    @keyframes arrive { from { opacity:0; transform:translateY(9px); } to { opacity:1; transform:translateY(0); } }
    @keyframes glimmer { from { opacity:0; transform:scale(.6) rotate(-10deg); } to { opacity:1; transform:scale(1) rotate(0); } }
    @media(max-width:560px) {
      .topbar { padding:22px 22px 16px; gap:15px; }
      .toptools { gap:12px; } .preview-label { display:none; }
      .preview-tools { gap:10px; } .versions button { padding:7px 10px; }
      .welcome { min-height:470px; padding-top:12px; padding-bottom:28px; }
      .handwriting { width:min(360px,100%); margin:0 0 8px; }
      .intro { font-size:14px; max-width:280px; }
      .highlights { grid-template-columns:repeat(2,minmax(0,1fr)); max-width:360px; gap:14px 20px; margin-top:24px; }
      .highlight span { font-size:11px; }
      .notes { margin-top:22px; }
      .brief-grid { grid-template-columns:1fr; }
      .brief-actions { align-items:flex-start; flex-direction:column; gap:9px; }
      .baseline-tag { display:none; }
      .chapter h2 { font-size:23px; }
      .note-name { font-size:15px; } .note-short { font-size:12px; }
      .notes-footer { align-items:flex-start; flex-direction:column; }
      .footnote { font-size:9px; letter-spacing:.12em; }
    }
    @media(pointer:coarse) { button,summary { min-height:44px; } }
    @media(max-width:360px) { .topbar { padding:18px 16px; } .toptools { gap:8px; } .brand { font-size:14px; gap:8px; } .quiet { font-size:11px; } }
    @media(prefers-reduced-motion:reduce) { *,*::before,*::after { animation:none !important; transition:none !important; } .handwriting .stroke { stroke-dashoffset:0; } }
  `;
  function releaseMark(version) {
    const digit=version==='1.8'
      ? 'M423 36 C395 22 365 47 373 74 C380 96 435 114 435 145 C435 176 390 190 371 164 C354 142 374 116 400 98 C423 81 451 56 423 36'
      : 'M439 79 C452 40 415 22 388 43 C355 68 356 113 386 116 C414 118 439 78 436 40 C445 78 424 137 401 166 C390 182 379 184 368 176';
    const stroke=(path,delay,duration)=>`<path class="stroke" data-draw-delay="${delay}" data-draw-duration="${duration}" d="${path}"/>`;
    return `<svg class="handwriting" viewBox="0 0 500 220" role="img" aria-label="HAI ${escapeHTML(version)}, animated handwritten release title">
      <defs><linearGradient id="hai-release-gradient" x1="20" y1="40" x2="450" y2="175" gradientUnits="userSpaceOnUse"><stop stop-color="#9bdfd9"/><stop offset=".43" stop-color="#c3b4fb"/><stop offset="1" stop-color="#efbdd3"/></linearGradient></defs>
      ${stroke('M25 175 Q39 100 45 35',.12,.36)}
      ${stroke('M106 35 Q91 106 89 172',.44,.36)}
      ${stroke('M36 111 Q69 104 97 106',.74,.24)}
      ${stroke('M115 175 Q146 101 169 37 Q174 28 178 48 L202 173',.9,.55)}
      ${stroke('M138 122 Q168 115 189 121',1.38,.22)}
      ${stroke('M246 37 L224 172',1.5,.3)}
      ${stroke('M226 40 Q242 35 257 38',1.5,.2)}
      ${stroke('M211 174 Q232 169 247 173',1.76,.2)}
      ${stroke('M278 77 Q302 60 314 39 L291 174',1.9,.46)}
      ${stroke('M274 175 Q298 171 318 173',2.28,.18)}
      ${stroke('M337 172 L338 172',2.45,.1)}
      ${stroke(digit,2.46,.68)}
      <g class="glimmer" fill="none" stroke="#e4c4ee" stroke-width="2.3" stroke-linecap="round"><path d="M477 31 Q476 42 467 44 Q478 44 480 55 Q478 43 489 42 Q478 42 477 31Z"/></g>
    </svg>`;
  }

  function animateReleaseTitle(root) {
    const paths=Array.from(root.querySelectorAll('.handwriting .stroke'));
    const animations=[];
    const reveal=path=>{path.style.strokeDasharray='none';path.style.strokeDashoffset='0';};
    if(matchMedia('(prefers-reduced-motion: reduce)').matches) return ()=>paths.forEach(reveal);
    for(const path of paths){
      if(typeof path.animate!=='function') {reveal(path);continue;}
      try{
        const length=path.getTotalLength()+2;
        path.style.strokeDasharray=`${length} ${length}`;
        path.style.strokeDashoffset=String(length);
        const animation=path.animate([{strokeDashoffset:String(length)},{strokeDashoffset:'0'}],{delay:Number(path.dataset.drawDelay)*1000,duration:Number(path.dataset.drawDuration)*1000,easing:'cubic-bezier(.35,.03,.25,1)',fill:'both'});
        animations.push(animation);
        animation.finished.then(()=>{reveal(path);animation.cancel();}).catch(()=>{reveal(path);});
      }catch(_){reveal(path);}
    }
    return ()=>{animations.forEach(animation=>animation.cancel());paths.forEach(reveal);};
  }

  function open(options = {}) {
    const version = String(options.version || '1.9');
    if (!RELEASES[version]) throw new RangeError('Supported HAI welcome versions: 1.8 and 1.9');
    const user = String(options.user || '');
    const preview = options.preview === true;
    if (!preview && !user) throw new TypeError('Provide the selected HAI account before showing a release welcome.');
    const key = `hai.welcome.seen.${encodeURIComponent(user)}.${version}`;
    if (!preview && !options.force) {
      try { if (localStorage.getItem(key)) return null; } catch (_) { /* Storage blocked: greeting remains usable. */ }
    }
    if (active) active.destroy();
    const previousFocus = document.activeElement;
    const host = document.createElement('hai-release-welcome');
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>${css}</style><dialog aria-labelledby="hai-welcome-heading"><div class="canvas">
      <div class="atmosphere" aria-hidden="true"><div class="halo a"></div><div class="halo b"></div><div class="halo c"></div></div>
      <div class="topbar"><div class="brand"><i aria-hidden="true"></i>HAI</div><div class="toptools">
        ${preview ? `<div class="preview-tools"><span class="preview-label">Design preview</span><div class="versions" role="group" aria-label="Preview release"><button type="button" data-version="1.8" aria-pressed="${version === '1.8'}">1.8</button><button type="button" data-version="1.9" aria-pressed="${version === '1.9'}">1.9</button></div><button class="quiet" type="button" data-action="replay">Replay</button></div>` : ''}
        <button class="quiet" type="button" data-action="skip">Skip <span aria-hidden="true">↗</span></button></div></div>
      <section class="welcome" aria-labelledby="hai-welcome-heading">${releaseMark(version)}<div class="arrival">
        <h1 id="hai-welcome-heading">Welcome back.</h1>
        <p class="intro">${escapeHTML(RELEASES[version].intro)}</p>
        <div class="highlights" aria-label="Highlights since 1.0"><div class="highlight"><strong>More than words</strong><span>Images, photo restyles, and personal portraits.</span></div><div class="highlight"><strong>Your work, live</strong><span>Follow progress, message HAI while it works, and ⑂ branch any answer.</span></div><div class="highlight"><strong>Your choices travel</strong><span>Model and effort preferences, across devices.</span></div><div class="highlight"><strong>A smarter little lab</strong><span>Measured model choices, clearer queues, and resume.</span></div></div>
        <div class="actions"><button class="primary" type="button" data-action="tour">Show me around <span aria-hidden="true">→</span></button><button class="text-button" type="button" data-action="summary">Just the highlights</button><button class="text-button" type="button" data-action="continue">Take me to HAI</button></div>
        <p class="love-note">made at home, for you <svg viewBox="0 0 22 22" aria-hidden="true"><path d="M11 18 S1 12 3 6 C5 1 10 4 11 7 C14 1 20 3 19 8 C18 13 11 18 11 18Z"/></svg></p>
      </div></section>
      <section class="brief" hidden aria-labelledby="hai-summary-heading"><button class="quiet" type="button" data-action="back">← Back to welcome</button><p class="kicker">HAI ${escapeHTML(version)} · the highlights</p><h1 id="hai-summary-heading">A few things to meet.</h1><p class="brief-intro">The changes you’ll notice first, since the HAI you launched.</p><div class="brief-grid">
        <article class="brief-item"><h2>A better home for your chats</h2><p>A searchable sidebar, a clearer phone layout, and live progress make it easier to find a conversation and follow the work. ⑂ Branch under any answer starts a new chat from that point and leaves the original as it was.</p></article>
        <article class="brief-item"><h2>Controls with clearer jobs</h2><p>Choose a text model, its thinking effort, and how it works. Your model and effort preferences now follow your account between devices.</p></article>
        <article class="brief-item"><h2>A creative side, in the same chat</h2><p>Ask for images, restyle uploaded photos, or use a saved identity model for a personal portrait when one is available to your account.</p></article>
        <article class="brief-item"><h2>Stay involved in longer work</h2><p>Send a correction while HAI works. Background replaces the old Absurd label; newer run recovery and finish checks help preserve and verify the work.</p></article>
        <article class="brief-item"><h2>A smarter lab behind the conversation</h2><p>Auto now uses request triage and a measured model menu. Quick gives speed more weight; a pinned model keeps a visible queue. More McCoy work and 2.0 are still ahead.</p></article>
        </div><div class="brief-actions"><button class="primary" type="button" data-action="tour">Take the interactive tour <span aria-hidden="true">→</span></button><button class="text-button" type="button" data-action="notes">Read the full catch-up</button><button class="text-button" type="button" data-action="continue">Take me to HAI</button></div><p class="brief-foot">The long thinking, hats, memory, and workspaces you knew are still here. These are the changes around and beyond them.</p></section>
      <section class="notes" hidden aria-labelledby="hai-notes-heading"><button class="quiet" type="button" data-action="summary">← Back to highlights</button><p class="kicker">HAI ${escapeHTML(version)} · since 1.0</p><h1 id="hai-notes-heading">The full catch-up.</h1><p class="intro">Open a topic for the details.</p>
        <details class="baseline"><summary>The HAI you already knew <span class="baseline-tag">Original foundations</span></summary><div><p>The long thinking, hats, and workspaces were already part of the original HAI. We kept building on them.</p><ul>${FOUNDATIONS.map(item=>`<li>${escapeHTML(item)}</li>`).join('')}</ul></div></details>
        <nav class="chapter-nav" aria-label="Release-note chapters"><button type="button" data-jump="0">Chat</button><button type="button" data-jump="1">Create</button><button type="button" data-jump="2">Work</button><button type="button" data-jump="3">The lab</button><button type="button" data-action="details">Expand all</button></nav>
        ${GROUPS.map((group,groupIndex) => `<section class="chapter" id="hai-chapter-${groupIndex}" aria-labelledby="hai-chapter-title-${groupIndex}"><p class="chapter-label">Chapter ${groupIndex+1} / ${GROUPS.length}</p><h2 id="hai-chapter-title-${groupIndex}">${escapeHTML(group.title)}</h2><p class="chapter-intro">${escapeHTML(group.intro)}</p>${NOTES.map((note,index) => note.group !== groupIndex ? '' : `<details class="note"><summary><span class="note-number" aria-hidden="true">${String(index + 1).padStart(2,'0')}</span><span><span class="note-name">${escapeHTML(note.name)}</span><span class="note-short">${escapeHTML(note.short)}</span></span><span class="plus" aria-hidden="true">+</span></summary><div class="note-details">${note.details.map(detail => `<p>${escapeHTML(detail)}</p>`).join('')}${note.example ? `<p class="example"><span>Something to try</span>“${escapeHTML(note.example)}”</p>`:''}</div></details>`).join('')}</section>`).join('')}
        <div class="notes-footer"><p class="thanks">Thanks for being part of this little lab.<br>Your feedback helps shape what comes next.</p><button class="primary" type="button" data-action="continue">Let’s go <span aria-hidden="true">→</span></button></div>
      </section>
      <section class="outro" hidden aria-labelledby="hai-outro-heading"><p class="kicker">Preview complete</p><h1 id="hai-outro-heading">Ready when you are.</h1><p>In HAI, this takes you back to your conversation.</p><button class="primary" type="button" data-action="replay">Watch it again <span aria-hidden="true">↻</span></button></section>
      <div class="footnote">${preview ? 'Preview · release copy is a draft' : 'Made with care. Powered by the home lab.'}</div>
    </div></dialog>`;
    document.body.append(host);
    const dialog = root.querySelector('dialog');
    const welcome = root.querySelector('.welcome');
    const notes = root.querySelector('.notes');
    const outro = root.querySelector('.outro');
    const screens={welcome,summary:root.querySelector('.brief'),notes,outro};
    let stopTitleAnimation=()=>{};
    let destroyed = false;
    function destroy() {
      if (destroyed) return;
      destroyed = true;
      stopTitleAnimation();
      if (dialog.open) dialog.close();
      host.remove();
      if (active && active.host === host) active = null;
      if (previousFocus && previousFocus.isConnected && typeof previousFocus.focus === 'function') previousFocus.focus();
    }
    function recordDismiss(reason) {
      if (!preview) {
        try { localStorage.setItem(key, new Date().toISOString()); } catch (_) { /* No persistence available. */ }
      }
      if (typeof options.onDismiss === 'function') options.onDismiss({ version, user, reason, preview });
    }
    function dismiss(reason) { destroy();recordDismiss(reason); }
    function launchTour(){
      if(!window.HAITour?.start){show('summary');return active;}
      destroy();
      const controller=window.HAITour.start({root:options.tourRoot||document,onFinish:()=>{active=null;recordDismiss('tour-complete');},onSkip:()=>{active=null;recordDismiss('tour-skipped');}});
      if(!controller.started)return open({...options,screen:'summary',force:true});
      active=controller;return controller;
    }
    function show(screen) {
      for(const [name,element] of Object.entries(screens)) element.hidden=name!==screen;
      const heading = {welcome:'hai-welcome-heading',summary:'hai-summary-heading',tour:'hai-tour-heading',notes:'hai-notes-heading',outro:'hai-outro-heading'}[screen];
      dialog.setAttribute('aria-labelledby', heading);
      dialog.scrollTop = 0;
      const section = screens[screen];
      (section.querySelector('button') || root.querySelector('[data-action="skip"]')).focus({ preventScroll:true });
    }
    root.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button) return;
      if (button.dataset.version) return open({ ...options, version:button.dataset.version });
      if (button.dataset.jump !== undefined) {
        root.getElementById('hai-chapter-'+button.dataset.jump).scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
        return;
      }
      switch (button.dataset.action) {
        case 'tour': launchTour(); break;
        case 'summary': show('summary'); break;
        case 'notes': show('notes'); break;
        case 'back': show('welcome'); break;
        case 'continue': dismiss('continue'); break;
        case 'skip': dismiss('skip'); break;
        case 'replay': open({ ...options, version, force:true }); break;
        case 'details': {
          const entries=Array.from(notes.querySelectorAll('.note'));
          const expand=entries.some(entry=>!entry.open);
          entries.forEach(entry=>{entry.open=expand;});
          break;
        }
      }
    });
    root.addEventListener('toggle', () => {
      const entries=Array.from(notes.querySelectorAll('.note'));
      root.querySelector('[data-action="details"]').textContent=entries.every(entry=>entry.open)?'Collapse all':'Expand all';
    }, true);
    dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss('escape'); });
    active = { host, root, destroy, version };
    dialog.showModal();
    stopTitleAnimation=animateReleaseTitle(root);
    // Put initial keyboard focus on an immediately usable action; no timed gate.
    if(options.screen==='tour')return launchTour();
    show(['summary','notes'].includes(options.screen)?options.screen:'welcome');
    return active;
  }
  window.HAIWelcome = Object.freeze({ open, versions:Object.keys(RELEASES), notes:NOTES, groups:GROUPS, foundations:FOUNDATIONS });
})();
