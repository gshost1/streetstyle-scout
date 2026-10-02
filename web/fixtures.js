// DEMO FIXTURES — not real VAST search results.
// Every moment here is invented for layout and state testing. Clip names,
// timestamps and descriptions do not correspond to real workshop footage.
// Replace with verified VAST frames + timestamps once the backend is wired.
//
// Shape (see API.md):
//   observed[]  = details Cosmos read clearly
//   uncertain[] = details that may be present but were not readable
// There are deliberately no fields for brands, faces, identity or demographics.

window.FIXTURES = {
  label: "DEMO FIXTURE",

  // Human-reviewed clips (from the Notion hub, 1:59 PM Oct 2). NOT rendered as
  // moments: the review lists attributes seen across a scene, not per person, so
  // turning them into per-person moments would require inventing the grouping.
  // The backend produces real moments from these clips; this list is reference only.
  reviewedClips: [
    { clip_id: "20261001_101025_sf4_chunk_0008.mp4", approx_seconds: 0, seen: ["purple/dark top", "patterned long skirt", "red top", "black backpack"] },
    { clip_id: "20261001_101141_sf4_chunk_0011.mp4", approx_seconds: 0, seen: ["dark sleeveless top", "dark pants", "dark-gray top", "light shorts", "dark outer layer", "light-pink skirt"] },
    { clip_id: "20261001_100753_sf4_chunk_0002.mp4", approx_seconds: 3, seen: ["dark outerwear", "gray hoodie", "red top/jacket", "bag/backpack"] },
  ],

  moments: [
    {
      id: "sf-001",
      collection: "San Francisco",
      clip_id: "demo-sf-clip-A.mp4",
      timestamp_seconds: 83.5,
      duration: 4,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.41, y: 0.16, w: 0.2, h: 0.66 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "puffer jacket" },
        { kind: "color", value: "bright yellow" },
        { kind: "accessory", value: "black backpack" },
      ],
      uncertain: [{ kind: "garment", value: "jeans or dark trousers", note: "legs partly out of frame" }],
      description:
        "A person walking left to right wearing a bright yellow puffer jacket, zipped, with a black backpack on both shoulders. Lower body is partly cut off by the frame edge.",
    },
    {
      id: "sf-002",
      collection: "San Francisco",
      clip_id: "demo-sf-clip-A.mp4",
      timestamp_seconds: 141.0,
      duration: 3,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.6, y: 0.2, w: 0.17, h: 0.6 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "windbreaker" },
        { kind: "color", value: "orange" },
        { kind: "accessory", value: "tote bag" },
      ],
      uncertain: [],
      description:
        "A person standing near a crosswalk in an orange windbreaker with the hood down, carrying a light canvas tote bag over one shoulder.",
    },
    {
      id: "sf-003",
      collection: "San Francisco",
      clip_id: "demo-sf-clip-B.mp4",
      timestamp_seconds: 12.2,
      duration: 5,
      frame_url: null,
      video_url: null,
      bounding_box: null,
      readability: "partial",
      observed: [
        { kind: "color", value: "red" },
        { kind: "accessory", value: "backpack" },
      ],
      uncertain: [
        { kind: "garment", value: "jacket or hoodie", note: "motion blur" },
        { kind: "color", value: "backpack color", note: "shadowed" },
      ],
      description:
        "A person moving quickly past the camera. A red upper garment is visible but its type is unclear because of motion blur. A backpack is visible; its color is in shadow.",
    },
    {
      id: "sf-004",
      collection: "San Francisco",
      clip_id: "demo-sf-clip-B.mp4",
      timestamp_seconds: 67.8,
      duration: 4,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.3, y: 0.22, w: 0.22, h: 0.62 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "trench coat" },
        { kind: "color", value: "beige" },
        { kind: "accessory", value: "umbrella" },
      ],
      uncertain: [{ kind: "accessory", value: "scarf", note: "may be the coat collar" }],
      description:
        "A person in a long beige trench coat holding a closed umbrella. Something around the neck could be a scarf or the coat's collar.",
    },
    {
      id: "sf-005",
      collection: "San Francisco",
      clip_id: "demo-sf-clip-C.mp4",
      timestamp_seconds: 203.4,
      duration: 3,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.48, y: 0.3, w: 0.14, h: 0.5 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "denim jacket" },
        { kind: "color", value: "light blue" },
        { kind: "accessory", value: "white sneakers" },
      ],
      uncertain: [],
      description:
        "A person seated on a bench wearing a light blue denim jacket over a dark top, with white sneakers.",
    },
    {
      id: "sf-006",
      collection: "San Francisco",
      clip_id: "demo-sf-clip-C.mp4",
      timestamp_seconds: 311.0,
      duration: 6,
      frame_url: null,
      video_url: null,
      bounding_box: null,
      readability: "unreadable",
      observed: [],
      uncertain: [
        { kind: "garment", value: "jacket", note: "silhouette only" },
        { kind: "color", value: "dark", note: "night footage, low light" },
      ],
      description:
        "Night footage. A person-shaped silhouette is present but clothing details are not readable at this exposure.",
    },
    {
      id: "to-001",
      collection: "Toronto",
      clip_id: "demo-to-clip-A.mp4",
      timestamp_seconds: 44.1,
      duration: 4,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.36, y: 0.14, w: 0.21, h: 0.7 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "rain jacket" },
        { kind: "color", value: "bright pink" },
        { kind: "accessory", value: "backpack" },
        { kind: "accessory", value: "beanie" },
      ],
      uncertain: [],
      description:
        "A person waiting at a streetcar stop in a bright pink rain jacket with a grey beanie and a small backpack.",
    },
    {
      id: "to-002",
      collection: "Toronto",
      clip_id: "demo-to-clip-A.mp4",
      timestamp_seconds: 98.6,
      duration: 3,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.55, y: 0.18, w: 0.18, h: 0.64 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "parka" },
        { kind: "color", value: "olive green" },
        { kind: "accessory", value: "crossbody bag" },
      ],
      uncertain: [{ kind: "garment", value: "boots", note: "lower frame cut off" }],
      description:
        "A person crossing the street in an olive green parka with the hood up, a crossbody bag across the chest.",
    },
    {
      id: "to-003",
      collection: "Toronto",
      clip_id: "demo-to-clip-B.mp4",
      timestamp_seconds: 17.3,
      duration: 5,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.2, y: 0.2, w: 0.2, h: 0.6 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "bomber jacket" },
        { kind: "color", value: "bright red" },
        { kind: "accessory", value: "headphones" },
      ],
      uncertain: [],
      description:
        "A person walking toward the camera in a bright red bomber jacket, wearing over-ear headphones.",
    },
    {
      id: "to-004",
      collection: "Toronto",
      clip_id: "demo-to-clip-B.mp4",
      timestamp_seconds: 152.9,
      duration: 4,
      frame_url: null,
      video_url: null,
      bounding_box: null,
      readability: "partial",
      observed: [
        { kind: "garment", value: "hoodie" },
        { kind: "color", value: "grey" },
      ],
      uncertain: [
        { kind: "accessory", value: "backpack or large bag", note: "seen from the front only" },
      ],
      description:
        "A person seen from the front in a grey hoodie. Straps over the shoulders suggest a backpack or large bag, not confirmed from this angle.",
    },
    {
      id: "to-005",
      collection: "Toronto",
      clip_id: "demo-to-clip-C.mp4",
      timestamp_seconds: 5.0,
      duration: 3,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.44, y: 0.12, w: 0.19, h: 0.72 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "leather jacket" },
        { kind: "color", value: "black" },
        { kind: "accessory", value: "sunglasses" },
      ],
      uncertain: [],
      description:
        "A person in a black leather jacket and sunglasses standing at a corner.",
    },
    {
      id: "to-006",
      collection: "Toronto",
      clip_id: "demo-to-clip-C.mp4",
      timestamp_seconds: 230.7,
      duration: 4,
      frame_url: null,
      video_url: null,
      bounding_box: { x: 0.5, y: 0.2, w: 0.2, h: 0.62 },
      readability: "clear",
      observed: [
        { kind: "garment", value: "vest" },
        { kind: "color", value: "lime green" },
        { kind: "accessory", value: "bicycle helmet" },
      ],
      uncertain: [{ kind: "garment", value: "cycling shorts", note: "partly behind the bike" }],
      description:
        "A cyclist stopped at a light in a lime green high-visibility vest and a white bicycle helmet.",
    },
  ],
};
