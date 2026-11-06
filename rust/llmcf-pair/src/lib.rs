//! llmcf-pair: the board's side of the framework's pairing by a short code (the hardware probe's second way of
//! pairing, beside the probe kit carried to the board). See main.rs for the program and proto.rs for the protocol.
#![forbid(unsafe_code)]

pub mod announce;
pub mod hostkeys;
pub mod local;
pub mod mdns;
pub mod proto;
