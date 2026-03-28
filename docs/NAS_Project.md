# 3TC(A) NAS Project

pierre.francois@insa-lyon.fr

---

## Objective

Automate the provisioning of BGP/MPLS VPN services  

GNS Project: Automate provisioning of Internet Services  
⇒ NAS Project: Add MPLS and BGP/MPLS VPN features  

You are allowed to work on the basis of your existing code base  

IPv4 :’)

### Repository automation (outillage du dépôt)

The repo ships a Python package **`cisco_intent`** driven by **`python -m cisco_intent`** (generate full configs from JSON intent, diff for incremental IOS snippets, telnet **push**, and **sync-startup** for Dynamips files). Paths default to repo root (`intent/`, `Configs/`, `modifs/`). See **[`docs/README.md`](README.md)** (French hub) and the root **`README.md`** for command examples.

---

## Phasing

- Phase 0: Setup  
- Phase 1: Core MPLS routing  
- Phase 2: Core BGP/MPLS VPN routing  
- Phase 3: Customer onboarding  
- Phase 4: More stuff  

---

## Phase 0: Setup

### Phase 0

- Groups (Group number, email w/everyone in cc to pfr and raz)

- GNS basic setup  
  - 4 routers in a row:  
    PE1 -- P1 -- P2 -- PE2

  - Addressing  
    - IPv4 Interfaces  
    - IPv4 Loopback Interfaces  

  - Routing  
    - OSPF (v2)  
    - Route loopbacks  

  - Validate routing and forwarding  

---

## Phase 1: Core MPLS routing

### Phase 1.a: LDP Config

- Enable LDP on your interfaces  

- Validate  
  - LDP session states  
  - MPLS transport in the core  
  - Penultimate Hop Popping behaviour  

---

### Phase 1.b: Automate

- Addressing  
- OSPF Routing  
- LDP  

---

## Phase 2: Core BGP/MPLS VPN routing

### Phase 2.a: Documentation

- Google: “Cisco IOS Basic BGP/MPLS VPN”  
  - Uses route reflection (optional)  
  - Uses IS-IS instead of OSPF (avoid)  

---

### Phase 2.b: Configuration

- Configure iBGP for vpnv4 address family  
- Loopback to Loopback iBGP sessions  

---

### Phase 2.c: Automate

- Addressing  
- OSPF  
- MPLS  
- BGP for vpnv4  

---

## Phase 3: Customer onboarding

### Phase 3.a: Add CE Routers, VRFs

- Add 4 CE routers (2 customers)  
- Configure VRF on PE routers  
- Associate VRF to the PE-CE interfaces  

---

### Phase 3.b: PE-CE Routing

- Configure eBGP as the PE-CE routing protocol  
  - Normal BGP config on the CE  
  - Normal BGP config in the VRF of the PE  

- Make networks attached to the CE routable  

- Validate routing  
  - No route leaking  

- Validate forwarding  

---

### Phase 3.c: Automate

- Automate:
  - VRFs  
  - Interface association  
  - eBGP in VRF  

- Book demo when working  

---

## Phase 4: Deeper

### Phase 4.a: Manageability

- Modify config without:
  - Reload  
  - Wipe  
  - Ghost config  

- Actions:
  - Add  
  - Delete  
  - Update  

---

### Phase 4.b: More Services

- Site sharing (multiple RTs)  

- Internet services  

- Ingress TE for dual-connected CE  

- RSVP  

---

## Project Checklist (current status)

### Phase 0: Setup

- [x] GNS basic setup with provider/customer routers in GNS3
- [x] IPv4 addressing on interfaces
- [x] IPv4 loopback addressing
- [x] OSPFv2 underlay configured
- [x] Loopbacks advertised in IGP
- [ ] Validation evidence of routing/forwarding stored (`show`/`ping`/`traceroute`)

### Phase 1: Core MPLS routing

- [x] LDP/MPLS enabled in core configurations
- [ ] Validation evidence: LDP sessions are up
- [ ] Validation evidence: MPLS transport is working end-to-end
- [ ] Validation evidence: PHP behavior confirmed

### Phase 1.b: Automate

- [x] Addressing automated
- [x] OSPF automated
- [x] LDP/MPLS automated

### Phase 2: Core BGP/MPLS VPN routing

- [x] Documentation produced (intent schema, examples, FAQ)
- [x] iBGP vpnv4 configured
- [x] Loopback-to-loopback iBGP sessions generated
- [x] Addressing automated
- [x] OSPF automated
- [x] MPLS automated
- [x] BGP vpnv4 automated

### Phase 3: Customer onboarding

- [x] CE routers integrated in topology
- [x] VRFs configured on PEs
- [x] VRF associated to PE-CE interfaces
- [x] PE-CE eBGP configured (CE global + PE VRF AF)
- [x] CE attached networks can be advertised (LAN + BGP)
- [ ] Validation evidence: no route leaking
- [ ] Validation evidence: forwarding tests archived
- [ ] Demo booked

### Phase 4: Deeper

- [X] Incremental config lifecycle (add/delete/update) without reload/wipe/ghost config — see **`cisco_intent diff`** + **`push`** in [`docs/README.md`](README.md)
- [ ] Site sharing (multiple RTs)
- [ ] Internet services
- [ ] Ingress TE for dual-connected CE
- [ ] RSVP

