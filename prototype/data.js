/* Taxonomy only.
   Cases and the signed-in user now come from the API (see server/).
   The procedure taxonomy stays client-side because it is a fixed
   controlled vocabulary the form and filters are both built from. */

const PROCEDURES = [
  { id: "endo", label: "Endodontics", children: [
      { id: "rct", label: "Root canal therapy" },
      { id: "retreat", label: "Endo retreatment" },
      { id: "apicoectomy", label: "Apicoectomy" } ] },
  { id: "surgery", label: "Oral Surgery", children: [
      { id: "extraction", label: "Extraction" },
      { id: "thirdmolar", label: "Third molar surgery" },
      { id: "graft", label: "Bone grafting" } ] },
  { id: "implant", label: "Implantology", children: [
      { id: "placement", label: "Implant placement" },
      { id: "sinuslift", label: "Sinus lift" },
      { id: "periimp", label: "Peri-implantitis" } ] },
  { id: "resto", label: "Restorative", children: [
      { id: "crown", label: "Crown & bridge" },
      { id: "composite", label: "Direct composite" } ] },
  { id: "perio", label: "Periodontics", children: [
      { id: "flap", label: "Flap surgery" } ] },
  { id: "ortho", label: "Orthodontics", children: [
      { id: "aligner", label: "Aligner therapy" } ] },
  { id: "pedo", label: "Pediatric", children: [
      { id: "pulpotomy", label: "Pulpotomy" } ] },
];

const COMPLICATIONS = [
  "Instrument separation", "Perforation", "Anatomical variation",
  "Hemorrhage", "Nerve injury", "Material failure",
  "Infection / flare-up", "Fracture", "Failed anesthesia",
];
