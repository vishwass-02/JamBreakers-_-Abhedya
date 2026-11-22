// The eight slides of the mission deck: seven guided scenarios + the live arena.
// Words come from /api/scenarios when the server is reachable (so they always match the simulation);
// FALLBACK keeps the deck usable on its own. LOOK holds what the 3D scene needs for each slide.

export const LOOK = {
  border_convoy: {
    accent: '#7cc4ff', emblem: 'mountain', sky: 'night', ground: '#0e1a1d', props: 'mountains',
    jammerPos: [22, 0, -14], road: [[-34, 10], [-14, 4], [4, 6], [22, 0], [40, -6]], drones: 2, trucks: 2,
  },
  urban_patrol: {
    accent: '#ffb347', emblem: 'city', sky: 'dusk', ground: '#1a1f2b', props: 'city',
    jammerPos: [14, 0, -8], road: [[-36, 8], [-10, 8], [6, 8], [6, -10], [36, -10]], drones: 3, trucks: 1,
  },
  coastal_watch: {
    accent: '#3fd0c9', emblem: 'ship', sky: 'dusk', ground: '#25321f', props: 'coast',
    jammerPos: [24, 0, -18], road: [[-36, 14], [-16, 10], [0, 12], [14, 16]], drones: 3, trucks: 1,
  },
  forest_ambush: {
    accent: '#7bd88f', emblem: 'tree', sky: 'night', ground: '#0c1a10', props: 'forest',
    jammerPos: [16, 0, -10], road: [[-36, 12], [-16, 4], [0, 2], [18, 8], [38, 4]], drones: 2, trucks: 2,
  },
  airbase_spoof: {
    accent: '#b48cff', emblem: 'plane', sky: 'night', ground: '#11141c', props: 'airbase',
    jammerPos: [26, 0, -16], road: [[-36, 16], [-12, 16], [10, 16], [36, 16]], drones: 3, trucks: 1,
  },
  relief_camp: {
    accent: '#ffd166', emblem: 'tent', sky: 'day', ground: '#3d5530', props: 'camp',
    jammerPos: [6, 0, -4], road: [[-36, 14], [-14, 10], [2, 12], [20, 16], [38, 12]], drones: 2, trucks: 1,
  },
  drone_delivery: {
    accent: '#ff7a59', emblem: 'drone', sky: 'sunset', ground: '#8a5f36', props: 'desert',
    jammerPos: [0, 0, 0], road: [[-36, 10], [-12, 6], [10, 10], [36, 4]], drones: 3, trucks: 1,
  },
  arena: {
    accent: '#00e5ff', emblem: 'radar', sky: 'night', ground: '#0f1d17', props: 'arena',
    jammerPos: [18, 0, -10], road: [[-36, 12], [-12, 8], [12, 12], [36, 8]], drones: 4, trucks: 2,
  },
};

// What the status chip says during each part of the 12 s story loop: clear → jammed → AI → restored.
export const STORY = {
  barrage: ['Links clear', 'Barrage floods all 8 channels', 'AI: hold channel, +9 dB, switch to BPSK', 'Link held through the noise'],
  random: ['Links clear', 'Random channels jammed', 'AI: blacklist its channels, hop to the quietest', 'Hopping around the noise'],
  sweep: ['Links clear', 'Sweep crossing every channel', 'AI: hold channel, +9 dB, then BPSK', 'Riding through the sweep'],
  reactive: ['Links clear', 'Jammer fires when it hears us', 'AI: keyed hopping, new channel every 10 ms', 'Gone before it reacts'],
  deceptive: ['Links clear', 'Fake commands injected', 'AI: forged frames rejected, hop away', '0 forged commands accepted'],
  accidental: ['Links clear', 'Friendly emitter in the band', 'AI: no alert, move quietly to a clean channel', 'Sharing the band politely'],
  follower: ['Links clear', 'Hostile drone follows our channel', 'AI: keyed hopping it cannot predict', 'The follower is lost'],
  arena: ['Open field, all links clear', 'Launch any jammer you like', 'AI names it and counters it', 'You play the enemy'],
};

export const ARENA = {
  id: 'arena', title: 'Live arena', subtitle: 'Free play: you are the enemy',
  jammer: 'arena', theme: 'open_field', time_of_day: 'night', duration_s: null,
  story: 'An open field with four drones, two trucks and a command post. Click the ground to drop any of the nine jammers, stack several at once, flip AI ON and OFF, compare side by side, run the security tests and the benchmark.',
  ai_response: 'The CNN names each jammer, the hybrid controller picks the counter-move, and every order passes the safety guard.',
  expected: 'Judges can join from their phones and attack together',
};

export const FALLBACK = [
  { id: 'border_convoy', title: 'Border convoy', subtitle: 'Barrage jamming in a mountain pass', jammer: 'barrage', theme: 'mountain_pass', time_of_day: 'night', duration_s: 90, story: 'A two-vehicle convoy crosses a mountain pass near the border with two escort drones overhead. A high-power barrage jammer on the far ridge floods the whole band with noise.', ai_response: 'CNN → barrage. Hopping is pointless when every channel is jammed, so: more power, then BPSK. Hold the channel.', expected: 'Fixed hopping ≈ 30–40 % delivered · ABHEDYA ≈ 90 %+' },
  { id: 'urban_patrol', title: 'Urban patrol', subtitle: 'Random-channel jammer hidden in a building', jammer: 'random', theme: 'city', time_of_day: 'dusk', duration_s: 90, story: 'A patrol moves through a city at dusk. A jammer hidden in a building hits random channels.', ai_response: 'CNN → random. Blacklist the channels it hits and hop among the rest.', expected: 'Fixed hopping ≈ 65–70 % · ABHEDYA ≈ 97 %+' },
  { id: 'coastal_watch', title: 'Coastal watch', subtitle: 'Sweep jammer on a ship offshore', jammer: 'sweep', theme: 'coast', time_of_day: 'dusk', duration_s: 90, story: 'Drones watch the coast. A ship offshore sweeps a jammer across the band.', ai_response: 'CNN → sweep. Predict where it goes next and hop ahead of it.', expected: 'Fixed hopping ≈ 25–40 % · ABHEDYA ≈ 85–95 %' },
  { id: 'forest_ambush', title: 'Forest ambush', subtitle: 'Reactive jammer that fires only when it hears us', jammer: 'reactive', theme: 'forest', time_of_day: 'night', duration_s: 90, story: 'A convoy in a forest at night. The jammer stays quiet until it hears us transmit.', ai_response: 'CNN → reactive. Short bursts and fast hops.', expected: 'Fixed hopping ≈ 85–88 % · ABHEDYA ≈ 93–95 %' },
  { id: 'airbase_spoof', title: 'Airbase spoofing', subtitle: 'Deceptive jammer injecting fake commands', jammer: 'deceptive', theme: 'airbase', time_of_day: 'night', duration_s: 90, story: 'An enemy injects fake "return to base" commands at an airbase.', ai_response: 'Every frame is signed and sealed: fakes are rejected.', expected: 'Fixed hopping ≈ 90 % (but forged frames keep arriving) · ABHEDYA ≈ 99.8 % · forged frames accepted: 0' },
  { id: 'relief_camp', title: 'Relief camp', subtitle: 'Accidental interference from friendly equipment', jammer: 'accidental', theme: 'camp', time_of_day: 'day', duration_s: 90, story: 'At a relief camp, friendly Wi-Fi spills into our band.', ai_response: 'CNN → accidental. Friendly, so just avoid the busy channel.', expected: 'Both stay high (≈ 98–100 %). The point: the AI does not over-react.' },
  { id: 'drone_delivery', title: 'Drone delivery', subtitle: 'Follower jammer on a hostile drone', jammer: 'follower', theme: 'desert', time_of_day: 'sunset', duration_s: 90, story: 'A delivery run over the desert. A hostile drone follows whatever channel we hop to.', ai_response: 'CNN → follower. Switch to keyed hopping it cannot predict.', expected: 'Fixed hopping ≈ 10–15 % · ABHEDYA ≈ 99 %' },
];
