//! Starting and stopping the board's engines (the model server, whisper, vision): each its own systemd unit, written
//! by llmcf-install, through systemctl with a fixed argv, a cleared environment and no shell. The units run outside
//! the gateway's memory limit; the gateway may start, stop and ask about exactly the units it was given
//! (llmcf-install's root action allow_unit_control allows nothing else). That right is a polkit rule (systemctl asks
//! polkit over D-Bus); a board without polkit's rules has a sudoers drop-in with exactly `systemctl start|stop|restart
//! <unit>` instead, so a start or stop that systemctl itself was not allowed is tried once more as `sudo -n
//! <systemctl> VERB UNIT`, the very line of that drop-in (sudo never asks: -n).

use std::path::Path;
use std::process::{Command, Stdio};

const SYSTEMCTL: &[&str] = &["/usr/bin/systemctl", "/bin/systemctl"];
const SUDO: &[&str] = &["/usr/bin/sudo", "/bin/sudo"];
pub const VERBS: &[&str] = &["start", "stop", "is-active"];

fn quiet(prog: &str, args: &[&str]) -> Result<bool, String> {
    let status = Command::new(prog)
        .args(args)
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("{prog} could not run: {e}"))?;
    Ok(status.success())
}

/// Runs `systemctl --no-ask-password VERB UNIT` (a start or stop it was not allowed: `sudo -n systemctl VERB UNIT`);
/// Ok(true) when it succeeded.
pub fn systemctl(verb: &str, unit: &str) -> Result<bool, String> {
    if !VERBS.contains(&verb) || !crate::args::is_unit(unit) {
        return Err("not a call the gateway makes".into());
    }
    let prog = SYSTEMCTL.iter().find(|p| Path::new(p).is_file()).ok_or("the board has no systemctl")?;
    if quiet(prog, &["--no-ask-password", verb, unit])? {
        return Ok(true);
    }
    match SUDO.iter().find(|p| Path::new(p).is_file()) {
        Some(sudo) if verb != "is-active" => quiet(sudo, &["-n", prog, verb, unit]),
        _ => Ok(false),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_its_own_verbs_and_units() {
        assert!(systemctl("restart", "llmcf-llm.service").is_err());
        assert!(systemctl("start", "sshd.service").is_err());
        assert!(systemctl("start", "llmcf-llm.service; reboot").is_err());
    }
}
