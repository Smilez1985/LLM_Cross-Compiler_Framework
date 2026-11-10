//! The strict JSON reader of the model card and the request options: serde_json's parser (no NaN, no Infinity, no
//! trailing text), plus the one rule it does not keep: an object that names a key twice is refused, as Python's side
//! refuses it (orchestrator/runtime/bundle/model_card.py), so both read every card the same way.

use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use std::fmt;

struct Strict(Value);

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Strict, D::Error> {
        d.deserialize_any(StrictVisitor).map(Strict)
    }
}

struct StrictVisitor;

impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("a JSON value")
    }
    fn visit_bool<E>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }
    fn visit_i64<E>(self, v: i64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }
    fn visit_u64<E>(self, v: u64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }
    fn visit_f64<E: serde::de::Error>(self, v: f64) -> Result<Value, E> {
        Number::from_f64(v).map(Value::Number).ok_or_else(|| E::custom("a number that is not finite"))
    }
    fn visit_str<E>(self, v: &str) -> Result<Value, E> {
        Ok(Value::String(v.to_owned()))
    }
    fn visit_string<E>(self, v: String) -> Result<Value, E> {
        Ok(Value::String(v))
    }
    fn visit_unit<E>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut out = Vec::new();
        while let Some(Strict(v)) = seq.next_element()? {
            out.push(v);
        }
        Ok(Value::Array(out))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut out = Map::new();
        while let Some(key) = map.next_key::<String>()? {
            let Strict(v) = map.next_value()?;
            if out.contains_key(&key) {
                return Err(serde::de::Error::custom("a key twice"));
            }
            out.insert(key, v);
        }
        Ok(Value::Object(out))
    }
}

/// `bytes` as one JSON value, at most `cap` bytes, strict (see the module text); the reason when it is not.
pub fn parse(bytes: &[u8], cap: usize) -> Result<Value, String> {
    if bytes.len() > cap {
        return Err(format!("larger than {} KiB", cap / 1024));
    }
    let text = std::str::from_utf8(bytes).map_err(|_| "not UTF-8".to_string())?;
    let mut de = serde_json::Deserializer::from_str(text);
    let Strict(v) = Strict::deserialize(&mut de).map_err(|e| format!("not strict JSON ({e})"))?;
    de.end().map_err(|_| "text after the JSON value".to_string())?;
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strict_json_is_taken_and_everything_else_refused() {
        assert_eq!(parse(br#"{"a":[1,2.5,"x",null,true]}"#, 1024).unwrap()["a"][1], 2.5);
        for bad in
            [&br#"{"a":1,"a":2}"#[..], b"{\"a\":NaN}", b"[1] x", b"{'a':1}", b"\xff", b"{\"a\":{\"b\":1,\"b\":1}}"]
        {
            assert!(parse(bad, 1024).is_err(), "{}", String::from_utf8_lossy(bad));
        }
        assert!(parse(&[b' '; 2048], 1024).unwrap_err().contains("larger"));
    }
}
