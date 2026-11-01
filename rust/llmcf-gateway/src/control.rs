//! Starting and stopping the board's model server: its own systemd unit (the llm component's, written by
//! llmcf-install), through systemctl with a fixed argv, a cleared environment and no shell. The unit runs outside the
//! gateway's memory limit; the gateway may start, stop and ask about exactly that unit (llmcf-install's polkit rule
//! allows nothing else).

use std::path::Path;
use std::process::{Command, Stdio};

const SYSTEMCTL: &[&str] = &["/usr/bin/systemctl", "/bin/systemctl"];
pub const VERBS: &[&str] = &["start", "stop", "is-active"];

/// Runs `systemctl --no-ask-password VERB UNIT`; Ok(true) when it succeeded.
pub fn systemctl(verb: &str, unit: &str) -> Result<bool, String> {
    if !VERBS.contains(&verb) || !crate::args::is_unit(unit) {
        return Err("not a call the gateway makes".into());
    }
    let prog = SYSTEMCTL.iter().find(|p| Path::new(p).is_file()).ok_or("the board has no systemctl")?;
    let status = Command::new(prog)
        .args(["--no-ask-password", verb, unit])
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LC_ALL", "C")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("systemctl could not run: {e}"))?;
    Ok(status.success())
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
