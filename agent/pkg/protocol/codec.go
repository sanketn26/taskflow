package protocol

import (
	"bytes"
	"encoding/binary"
	"math"
	"sort"
	"unicode/utf8"
)

// --------------------------------------------------------------------
// Canonical encode primitives: minimal headers for maps/arrays/strings
// /binary, fixed width for the uint32/uint64/int64 fields the schemas
// declare. Library struct-tag marshaling is deliberately not used here:
// it cannot guarantee sorted keys, fixed integer widths, or rejection of
// unknown fields.
// --------------------------------------------------------------------

func packNil() []byte { return []byte{0xc0} }

func packBool(v bool) []byte {
	if v {
		return []byte{0xc3}
	}
	return []byte{0xc2}
}

func packU32(v uint32) []byte {
	b := make([]byte, 5)
	b[0] = 0xce
	binary.BigEndian.PutUint32(b[1:], v)
	return b
}

func packU64(v uint64) []byte {
	b := make([]byte, 9)
	b[0] = 0xcf
	binary.BigEndian.PutUint64(b[1:], v)
	return b
}

func packI64(v int64) []byte {
	b := make([]byte, 9)
	b[0] = 0xd3
	binary.BigEndian.PutUint64(b[1:], uint64(v))
	return b
}

func packFloat64(v float64) []byte {
	b := make([]byte, 9)
	b[0] = 0xcb
	binary.BigEndian.PutUint64(b[1:], math.Float64bits(v))
	return b
}

func packPortableInt(v int64) []byte {
	if v >= 0 {
		return packPortableUint(uint64(v))
	}
	if v >= -32 {
		return []byte{byte(int8(v))}
	}
	if v >= -128 {
		return []byte{0xd0, byte(int8(v))}
	}
	if v >= -32768 {
		b := []byte{0xd1, 0, 0}
		binary.BigEndian.PutUint16(b[1:], uint16(int16(v)))
		return b
	}
	if v >= -2147483648 {
		b := []byte{0xd2, 0, 0, 0, 0}
		binary.BigEndian.PutUint32(b[1:], uint32(int32(v)))
		return b
	}
	return packI64(v)
}
func packPortableUint(v uint64) []byte {
	if v <= 0x7f {
		return []byte{byte(v)}
	}
	if v <= 0xff {
		return []byte{0xcc, byte(v)}
	}
	if v <= 0xffff {
		b := []byte{0xcd, 0, 0}
		binary.BigEndian.PutUint16(b[1:], uint16(v))
		return b
	}
	if v <= 0xffffffff {
		b := []byte{0xce, 0, 0, 0, 0}
		binary.BigEndian.PutUint32(b[1:], uint32(v))
		return b
	}
	return packU64(v)
}

func packStr(s string) []byte {
	data := []byte(s)
	n := len(data)
	switch {
	case n <= 31:
		return append([]byte{0xa0 | byte(n)}, data...)
	case n <= 0xff:
		return append([]byte{0xd9, byte(n)}, data...)
	case n <= 0xffff:
		h := make([]byte, 3)
		h[0] = 0xda
		binary.BigEndian.PutUint16(h[1:], uint16(n))
		return append(h, data...)
	default:
		h := make([]byte, 5)
		h[0] = 0xdb
		binary.BigEndian.PutUint32(h[1:], uint32(n))
		return append(h, data...)
	}
}

func packBin(b []byte) []byte {
	n := len(b)
	switch {
	case n <= 0xff:
		return append([]byte{0xc4, byte(n)}, b...)
	case n <= 0xffff:
		h := make([]byte, 3)
		h[0] = 0xc5
		binary.BigEndian.PutUint16(h[1:], uint16(n))
		return append(h, b...)
	default:
		h := make([]byte, 5)
		h[0] = 0xc6
		binary.BigEndian.PutUint32(h[1:], uint32(n))
		return append(h, b...)
	}
}

func arrayHeader(n int) []byte {
	switch {
	case n <= 15:
		return []byte{0x90 | byte(n)}
	case n <= 0xffff:
		b := make([]byte, 3)
		b[0] = 0xdc
		binary.BigEndian.PutUint16(b[1:], uint16(n))
		return b
	default:
		b := make([]byte, 5)
		b[0] = 0xdd
		binary.BigEndian.PutUint32(b[1:], uint32(n))
		return b
	}
}

func mapHeaderBytes(n int) []byte {
	switch {
	case n <= 15:
		return []byte{0x80 | byte(n)}
	case n <= 0xffff:
		b := make([]byte, 3)
		b[0] = 0xde
		binary.BigEndian.PutUint16(b[1:], uint16(n))
		return b
	default:
		b := make([]byte, 5)
		b[0] = 0xdf
		binary.BigEndian.PutUint32(b[1:], uint32(n))
		return b
	}
}

func packArray(items [][]byte) []byte {
	out := arrayHeader(len(items))
	for _, it := range items {
		out = append(out, it...)
	}
	return out
}

type mapField struct {
	key   string
	value []byte
}

func packMap(fields []mapField) []byte {
	sorted := make([]mapField, len(fields))
	copy(sorted, fields)
	sort.Slice(sorted, func(i, j int) bool {
		return bytes.Compare([]byte(sorted[i].key), []byte(sorted[j].key)) < 0
	})
	out := mapHeaderBytes(len(sorted))
	for _, f := range sorted {
		out = append(out, packStr(f.key)...)
		out = append(out, f.value...)
	}
	return out
}

func packStrMap(m map[string]string) []byte {
	fields := make([]mapField, 0, len(m))
	for k, v := range m {
		fields = append(fields, mapField{k, packStr(v)})
	}
	return packMap(fields)
}

func packStrU64Map(m map[string]uint64) []byte {
	fields := make([]mapField, 0, len(m))
	for k, v := range m {
		fields = append(fields, mapField{k, packU64(v)})
	}
	return packMap(fields)
}

// --------------------------------------------------------------------
// Strict decode: hand-rolled recursive-descent msgpack reader producing
// Go's nil / bool / string / []byte / uint64 / int64 / []interface{} /
// map[string]interface{}, with duplicate-key and invalid-UTF-8 rejection.
// --------------------------------------------------------------------

type byteCursor struct {
	data []byte
	pos  int
}

func (c *byteCursor) u8() (byte, error) {
	if c.pos >= len(c.data) {
		return 0, NewDecodeError(MalformedPayload, "unexpected end of payload")
	}
	b := c.data[c.pos]
	c.pos++
	return b, nil
}

func (c *byteCursor) take(n int) ([]byte, error) {
	if n < 0 || c.pos+n > len(c.data) {
		return nil, NewDecodeError(MalformedPayload, "unexpected end of payload")
	}
	out := c.data[c.pos : c.pos+n]
	c.pos += n
	return out, nil
}

func (c *byteCursor) u16() (uint16, error) {
	b, err := c.take(2)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint16(b), nil
}

func (c *byteCursor) u32() (uint32, error) {
	b, err := c.take(4)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint32(b), nil
}

func (c *byteCursor) u64() (uint64, error) {
	b, err := c.take(8)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint64(b), nil
}

func decodeStr(data []byte) (string, error) {
	if !utf8.Valid(data) {
		return "", NewDecodeError(MalformedPayload, "invalid UTF-8 string")
	}
	return string(data), nil
}

func decodeValue(c *byteCursor) (interface{}, error) {
	b, err := c.u8()
	if err != nil {
		return nil, err
	}

	switch {
	case b <= 0x7f:
		return uint64(b), nil
	case b >= 0xe0:
		return int64(int8(b)), nil
	case b&0xe0 == 0xa0:
		n := int(b & 0x1f)
		raw, err := c.take(n)
		if err != nil {
			return nil, err
		}
		return decodeStr(raw)
	case b&0xf0 == 0x90:
		return decodeArray(c, int(b&0x0f))
	case b&0xf0 == 0x80:
		return decodeMap(c, int(b&0x0f))
	}

	switch b {
	case 0xc0:
		return nil, nil
	case 0xc2:
		return false, nil
	case 0xc3:
		return true, nil
	case 0xc4:
		n, err := c.u8()
		if err != nil {
			return nil, err
		}
		return c.take(int(n))
	case 0xc5:
		n, err := c.u16()
		if err != nil {
			return nil, err
		}
		return c.take(int(n))
	case 0xc6:
		n, err := c.u32()
		if err != nil {
			return nil, err
		}
		return c.take(int(n))
	case 0xcc:
		n, err := c.u8()
		if err != nil {
			return nil, err
		}
		return uint64(n), nil
	case 0xcd:
		n, err := c.u16()
		if err != nil {
			return nil, err
		}
		return uint64(n), nil
	case 0xce:
		n, err := c.u32()
		if err != nil {
			return nil, err
		}
		return uint64(n), nil
	case 0xcf:
		return c.u64()
	case 0xcb:
		n, err := c.u64()
		if err != nil {
			return nil, err
		}
		return math.Float64frombits(n), nil
	case 0xd0:
		n, err := c.u8()
		if err != nil {
			return nil, err
		}
		return int64(int8(n)), nil
	case 0xd1:
		n, err := c.u16()
		if err != nil {
			return nil, err
		}
		return int64(int16(n)), nil
	case 0xd2:
		n, err := c.u32()
		if err != nil {
			return nil, err
		}
		return int64(int32(n)), nil
	case 0xd3:
		n, err := c.u64()
		if err != nil {
			return nil, err
		}
		return int64(n), nil
	case 0xd9:
		n, err := c.u8()
		if err != nil {
			return nil, err
		}
		raw, err := c.take(int(n))
		if err != nil {
			return nil, err
		}
		return decodeStr(raw)
	case 0xda:
		n, err := c.u16()
		if err != nil {
			return nil, err
		}
		raw, err := c.take(int(n))
		if err != nil {
			return nil, err
		}
		return decodeStr(raw)
	case 0xdb:
		n, err := c.u32()
		if err != nil {
			return nil, err
		}
		raw, err := c.take(int(n))
		if err != nil {
			return nil, err
		}
		return decodeStr(raw)
	case 0xdc:
		n, err := c.u16()
		if err != nil {
			return nil, err
		}
		return decodeArray(c, int(n))
	case 0xdd:
		n, err := c.u32()
		if err != nil {
			return nil, err
		}
		return decodeArray(c, int(n))
	case 0xde:
		n, err := c.u16()
		if err != nil {
			return nil, err
		}
		return decodeMap(c, int(n))
	case 0xdf:
		n, err := c.u32()
		if err != nil {
			return nil, err
		}
		return decodeMap(c, int(n))
	}

	return nil, NewDecodeError(MalformedPayload, "unsupported msgpack type byte")
}

func decodeArray(c *byteCursor, n int) ([]interface{}, error) {
	out := make([]interface{}, 0, n)
	for i := 0; i < n; i++ {
		v, err := decodeValue(c)
		if err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, nil
}

func decodeMap(c *byteCursor, n int) (map[string]interface{}, error) {
	out := make(map[string]interface{}, n)
	for i := 0; i < n; i++ {
		kv, err := decodeValue(c)
		if err != nil {
			return nil, err
		}
		key, ok := kv.(string)
		if !ok {
			return nil, NewDecodeError(InvalidMessage, "map key must be a string")
		}
		if _, dup := out[key]; dup {
			return nil, NewDecodeError(InvalidMessage, "duplicate key "+key)
		}
		v, err := decodeValue(c)
		if err != nil {
			return nil, err
		}
		out[key] = v
	}
	return out, nil
}

// EncodePortableValue encodes the language-neutral application value subset.
func EncodePortableValue(value interface{}) ([]byte, error) {
	switch v := value.(type) {
	case nil:
		return packNil(), nil
	case bool:
		return packBool(v), nil
	case int:
		return packPortableInt(int64(v)), nil
	case int8:
		return packPortableInt(int64(v)), nil
	case int16:
		return packPortableInt(int64(v)), nil
	case int32:
		return packPortableInt(int64(v)), nil
	case int64:
		return packPortableInt(v), nil
	case uint:
		return packPortableUint(uint64(v)), nil
	case uint8:
		return packPortableUint(uint64(v)), nil
	case uint16:
		return packPortableUint(uint64(v)), nil
	case uint32:
		return packPortableUint(uint64(v)), nil
	case uint64:
		return packPortableUint(v), nil
	case float64:
		if !math.IsNaN(v) && !math.IsInf(v, 0) {
			return packFloat64(v), nil
		}
	case string:
		return packStr(v), nil
	case []byte:
		return packBin(v), nil
	case []interface{}:
		items := make([][]byte, len(v))
		for i, x := range v {
			b, e := EncodePortableValue(x)
			if e != nil {
				return nil, e
			}
			items[i] = b
		}
		return packArray(items), nil
	case map[string]interface{}:
		fields := make([]mapField, 0, len(v))
		for k, x := range v {
			b, e := EncodePortableValue(x)
			if e != nil {
				return nil, e
			}
			fields = append(fields, mapField{k, b})
		}
		return packMap(fields), nil
	}
	return nil, NewDecodeError(InvalidMessage, "value is outside the portable msgpack profile")
}

// DecodePortableValue rejects extension types, non-string keys, non-finite
// floats, trailing bytes, and values outside the v1 portable profile.
func DecodePortableValue(payload []byte) (interface{}, error) {
	v, e := unpackStrict(payload)
	if e != nil {
		return nil, e
	}
	if e = validatePortableValue(v); e != nil {
		return nil, e
	}
	return v, nil
}
func validatePortableValue(v interface{}) error {
	switch x := v.(type) {
	case nil, bool, int64, uint64, string, []byte:
		return nil
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) {
			return NewDecodeError(InvalidMessage, "portable float must be finite")
		}
		return nil
	case []interface{}:
		for _, item := range x {
			if e := validatePortableValue(item); e != nil {
				return e
			}
		}
		return nil
	case map[string]interface{}:
		for _, item := range x {
			if e := validatePortableValue(item); e != nil {
				return e
			}
		}
		return nil
	}
	return NewDecodeError(InvalidMessage, "value is outside the portable msgpack profile")
}

// unpackStrict decodes exactly one msgpack value from payload and rejects
// trailing bytes.
func unpackStrict(payload []byte) (interface{}, error) {
	c := &byteCursor{data: payload}
	v, err := decodeValue(c)
	if err != nil {
		return nil, err
	}
	if c.pos != len(payload) {
		return nil, NewDecodeError(InvalidMessage, "trailing bytes after payload")
	}
	return v, nil
}
