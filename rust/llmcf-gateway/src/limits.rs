//! The rate limit: a token bucket per client address (rate_per_minute requests, refilled evenly), with a bounded
//! table so many addresses cannot grow it without end.

use std::collections::HashMap;
use std::net::IpAddr;
use std::time::Instant;

const MAX_PEERS: usize = 4096;

pub struct RateLimit {
    per_minute: f64,
    buckets: HashMap<IpAddr, (f64, Instant)>,
}

impl RateLimit {
    pub fn new(per_minute: u32) -> RateLimit {
        RateLimit { per_minute: per_minute.max(1) as f64, buckets: HashMap::new() }
    }

    /// Ok when the request may go now, else the seconds until the next one may.
    pub fn take(&mut self, peer: IpAddr, now: Instant) -> Result<(), u64> {
        if self.buckets.len() >= MAX_PEERS && !self.buckets.contains_key(&peer) {
            let per_minute = self.per_minute;
            self.buckets.retain(|_, (tokens, at)| {
                *tokens + now.duration_since(*at).as_secs_f64() * per_minute / 60.0 < per_minute
            });
            if self.buckets.len() >= MAX_PEERS {
                return Err(60);
            }
        }
        let (tokens, at) = self.buckets.entry(peer).or_insert((self.per_minute, now));
        let refill = now.duration_since(*at).as_secs_f64() * self.per_minute / 60.0;
        *tokens = (*tokens + refill).min(self.per_minute);
        *at = now;
        if *tokens >= 1.0 {
            *tokens -= 1.0;
            Ok(())
        } else {
            Err(((1.0 - *tokens) * 60.0 / self.per_minute).ceil().max(1.0) as u64)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn the_bucket_empties_and_refills() {
        let mut r = RateLimit::new(2);
        let ip: IpAddr = "192.0.2.1".parse().unwrap();
        let t = Instant::now();
        assert!(r.take(ip, t).is_ok());
        assert!(r.take(ip, t).is_ok());
        let wait = r.take(ip, t).unwrap_err();
        assert!((1..=30).contains(&wait));
        assert!(r.take(ip, t + Duration::from_secs(31)).is_ok());
        let other: IpAddr = "198.51.100.7".parse().unwrap();
        assert!(r.take(other, t).is_ok());
    }
}
