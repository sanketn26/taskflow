package protocol

// --------------------------------------------------------------------
// Generic decode helpers operating on the map[string]interface{} /
// []interface{} tree produced by unpackStrict.
// --------------------------------------------------------------------

func asMap(v interface{}, field string) (map[string]interface{}, error) {
	m, ok := v.(map[string]interface{})
	if !ok {
		return nil, NewDecodeError(InvalidMessage, field+" must be a map")
	}
	return m, nil
}

func reqField(m map[string]interface{}, key string) (interface{}, error) {
	v, ok := m[key]
	if !ok {
		return nil, NewDecodeError(InvalidMessage, "missing required key "+key)
	}
	return v, nil
}

func setOf(keys ...string) map[string]bool {
	out := make(map[string]bool, len(keys))
	for _, k := range keys {
		out[k] = true
	}
	return out
}

func noUnknown(m map[string]interface{}, allowed map[string]bool) error {
	for k := range m {
		if !allowed[k] {
			return NewDecodeError(InvalidMessage, "unknown key "+k)
		}
	}
	return nil
}

func keySetEquals(m map[string]interface{}, want map[string]bool) bool {
	if len(m) != len(want) {
		return false
	}
	for k := range want {
		if _, ok := m[k]; !ok {
			return false
		}
	}
	return true
}

func keySetSuperset(m map[string]interface{}, want map[string]bool) bool {
	for k := range want {
		if _, ok := m[k]; !ok {
			return false
		}
	}
	return true
}

func asBin(v interface{}, n int, field string) ([]byte, error) {
	b, ok := v.([]byte)
	if !ok || len(b) != n {
		return nil, NewDecodeError(InvalidMessage, field+" must be a fixed-size binary value")
	}
	return b, nil
}

func asBinAny(v interface{}, field string) ([]byte, error) {
	b, ok := v.([]byte)
	if !ok {
		return nil, NewDecodeError(InvalidMessage, field+" must be binary")
	}
	return b, nil
}

func asStr(v interface{}, field string) (string, error) {
	s, ok := v.(string)
	if !ok {
		return "", NewDecodeError(InvalidMessage, field+" must be a string")
	}
	return s, nil
}

func asBool(v interface{}, field string) (bool, error) {
	b, ok := v.(bool)
	if !ok {
		return false, NewDecodeError(InvalidMessage, field+" must be a bool")
	}
	return b, nil
}

func asUint(v interface{}, bits int, field string) (uint64, error) {
	var u uint64
	switch n := v.(type) {
	case uint64:
		u = n
	case int64:
		if n < 0 {
			return 0, NewDecodeError(InvalidMessage, field+" out of range")
		}
		u = uint64(n)
	default:
		return 0, NewDecodeError(InvalidMessage, field+" must be an integer")
	}
	if bits < 64 && u >= (uint64(1)<<uint(bits)) {
		return 0, NewDecodeError(InvalidMessage, field+" out of range")
	}
	return u, nil
}

func asInt(v interface{}, bits int, field string) (int64, error) {
	var n int64
	switch t := v.(type) {
	case int64:
		n = t
	case uint64:
		if t > (uint64(1)<<uint(bits-1))-1 {
			return 0, NewDecodeError(InvalidMessage, field+" out of range")
		}
		n = int64(t)
	default:
		return 0, NewDecodeError(InvalidMessage, field+" must be an integer")
	}
	lo := -(int64(1) << uint(bits-1))
	hi := (int64(1) << uint(bits-1)) - 1
	if n < lo || n > hi {
		return 0, NewDecodeError(InvalidMessage, field+" out of range")
	}
	return n, nil
}

func asStrMap(v interface{}, field string) (map[string]string, error) {
	m, ok := v.(map[string]interface{})
	if !ok {
		return nil, NewDecodeError(InvalidMessage, field+" must be a map")
	}
	out := make(map[string]string, len(m))
	for k, val := range m {
		s, ok := val.(string)
		if !ok {
			return nil, NewDecodeError(InvalidMessage, field+" must be map<string,string>")
		}
		out[k] = s
	}
	return out, nil
}

func asArray(v interface{}, field string) ([]interface{}, error) {
	a, ok := v.([]interface{})
	if !ok {
		return nil, NewDecodeError(InvalidMessage, field+" must be an array")
	}
	return a, nil
}

func asEnum(v interface{}, allowed map[string]bool, field string) (string, error) {
	s, err := asStr(v, field)
	if err != nil {
		return "", err
	}
	if !allowed[s] {
		return "", NewDecodeError(InvalidMessage, field+" has unknown value "+s)
	}
	return s, nil
}

func decodeOptional[T any](v interface{}, decode func(interface{}) (T, error)) (*T, error) {
	if v == nil {
		return nil, nil
	}
	d, err := decode(v)
	if err != nil {
		return nil, err
	}
	return &d, nil
}

func packOptional(v []byte, present bool) []byte {
	if !present {
		return packNil()
	}
	return v
}

// --------------------------------------------------------------------
// ObjectRef / ValueRef
// --------------------------------------------------------------------

type ObjectRef struct {
	Store  string
	Key    string
	Size   uint64
	SHA256 []byte
	Codec  string
}

func (o *ObjectRef) Validate() error {
	if o == nil || o.Store == "" || o.Key == "" || o.Codec == "" || len(o.SHA256) != 32 {
		return NewDecodeError(InvalidMessage, "invalid ObjectRef")
	}
	return nil
}

func (o *ObjectRef) Encode() []byte {
	return packMap([]mapField{
		{"store", packStr(o.Store)},
		{"key", packStr(o.Key)},
		{"size", packU64(o.Size)},
		{"sha256", packBin(o.SHA256)},
		{"codec", packStr(o.Codec)},
	})
}

var objectRefKeys = setOf("store", "key", "size", "sha256", "codec")

func DecodeObjectRef(m map[string]interface{}) (*ObjectRef, error) {
	if err := noUnknown(m, objectRefKeys); err != nil {
		return nil, err
	}
	store, err := reqField(m, "store")
	if err != nil {
		return nil, err
	}
	storeStr, err := asStr(store, "store")
	if err != nil {
		return nil, err
	}
	key, err := reqField(m, "key")
	if err != nil {
		return nil, err
	}
	keyStr, err := asStr(key, "key")
	if err != nil {
		return nil, err
	}
	sizeV, err := reqField(m, "size")
	if err != nil {
		return nil, err
	}
	size, err := asUint(sizeV, 64, "size")
	if err != nil {
		return nil, err
	}
	shaV, err := reqField(m, "sha256")
	if err != nil {
		return nil, err
	}
	sha, err := asBin(shaV, 32, "sha256")
	if err != nil {
		return nil, err
	}
	codecV, err := reqField(m, "codec")
	if err != nil {
		return nil, err
	}
	codec, err := asStr(codecV, "codec")
	if err != nil {
		return nil, err
	}
	return &ObjectRef{Store: storeStr, Key: keyStr, Size: size, SHA256: sha, Codec: codec}, nil
}

// ValueRef is exactly one of {inline, codec} or {object}. Use
// NewInlineValueRef / NewObjectValueRef to construct one; Encode
// validates before producing bytes so an invalid union can never be
// encoded.
type ValueRef struct {
	Inline []byte
	Codec  string
	Object *ObjectRef
}

func NewInlineValueRef(data []byte, codec string) *ValueRef {
	return &ValueRef{Inline: data, Codec: codec}
}

func NewObjectValueRef(obj *ObjectRef) *ValueRef {
	return &ValueRef{Object: obj}
}

func (v *ValueRef) Validate() error {
	hasInline := v.Inline != nil
	hasObject := v.Object != nil
	if hasInline == hasObject {
		return NewDecodeError(InvalidMessage, "ValueRef requires exactly one of inline or object")
	}
	if hasInline && v.Codec == "" {
		return NewDecodeError(InvalidMessage, "ValueRef.inline requires codec")
	}
	if hasObject {
		return v.Object.Validate()
	}
	return nil
}

func (v *ValueRef) Encode() ([]byte, error) {
	if err := v.Validate(); err != nil {
		return nil, err
	}
	if v.Object != nil {
		return packMap([]mapField{{"object", v.Object.Encode()}}), nil
	}
	return packMap([]mapField{
		{"inline", packBin(v.Inline)},
		{"codec", packStr(v.Codec)},
	}), nil
}

func DecodeValueRef(m map[string]interface{}) (*ValueRef, error) {
	if keySetEquals(m, setOf("object")) {
		objV, _ := reqField(m, "object")
		objMap, err := asMap(objV, "object")
		if err != nil {
			return nil, err
		}
		obj, err := DecodeObjectRef(objMap)
		if err != nil {
			return nil, err
		}
		return NewObjectValueRef(obj), nil
	}
	if keySetEquals(m, setOf("inline", "codec")) {
		inlineV, _ := reqField(m, "inline")
		inline, err := asBinAny(inlineV, "inline")
		if err != nil {
			return nil, err
		}
		codecV, _ := reqField(m, "codec")
		codec, err := asStr(codecV, "codec")
		if err != nil {
			return nil, err
		}
		return NewInlineValueRef(inline, codec), nil
	}
	return nil, NewDecodeError(InvalidMessage, "ValueRef has invalid key set")
}

// --------------------------------------------------------------------
// Connection / request schemas
// --------------------------------------------------------------------

var helloRoles = setOf("runtime", "worker", "admin")
var workerRuntimes = setOf("python", "nodejs", "go")
var invocationProfiles = setOf("value", "python_args")

type Hello struct {
	Role           string
	OwnerID        []byte
	WorkerID       string
	Runtime        string
	RuntimeVersion string
	SDKVersion     string
	Codecs         []string
}

func NewRuntimeHello(ownerID []byte) *Hello { return &Hello{Role: "runtime", OwnerID: ownerID} }
func NewWorkerHello(workerID, runtimeName, runtimeVersion, sdkVersion string, codecs []string) *Hello {
	return &Hello{Role: "worker", WorkerID: workerID, Runtime: runtimeName, RuntimeVersion: runtimeVersion, SDKVersion: sdkVersion, Codecs: codecs}
}
func NewAdminHello() *Hello { return &Hello{Role: "admin"} }

func (h *Hello) Validate() error {
	if !helloRoles[h.Role] {
		return NewDecodeError(InvalidMessage, "unknown Hello role")
	}
	switch h.Role {
	case "runtime":
		if len(h.OwnerID) != 16 || h.WorkerID != "" || h.Runtime != "" || h.RuntimeVersion != "" || h.SDKVersion != "" || len(h.Codecs) != 0 {
			return NewDecodeError(InvalidMessage, "runtime Hello requires only owner_id")
		}
	case "worker":
		if h.WorkerID == "" || h.OwnerID != nil || !workerRuntimes[h.Runtime] || h.RuntimeVersion == "" || h.SDKVersion == "" || len(h.Codecs) == 0 {
			return NewDecodeError(InvalidMessage, "worker Hello requires identity, runtime, versions, and codecs")
		}
		seen := make(map[string]bool, len(h.Codecs))
		for _, codec := range h.Codecs {
			if codec == "" || seen[codec] {
				return NewDecodeError(InvalidMessage, "worker Hello codecs must be non-empty and unique")
			}
			seen[codec] = true
		}
	case "admin":
		if h.OwnerID != nil || h.WorkerID != "" || h.Runtime != "" || h.RuntimeVersion != "" || h.SDKVersion != "" || len(h.Codecs) != 0 {
			return NewDecodeError(InvalidMessage, "admin Hello takes no extra fields")
		}
	}
	return nil
}

func (h *Hello) Encode() ([]byte, error) {
	if err := h.Validate(); err != nil {
		return nil, err
	}
	fields := []mapField{{"role", packStr(h.Role)}}
	if h.OwnerID != nil {
		fields = append(fields, mapField{"owner_id", packBin(h.OwnerID)})
	}
	if h.WorkerID != "" {
		fields = append(fields, mapField{"worker_id", packStr(h.WorkerID)})
		fields = append(fields, mapField{"runtime", packStr(h.Runtime)}, mapField{"runtime_version", packStr(h.RuntimeVersion)}, mapField{"sdk_version", packStr(h.SDKVersion)})
		codecItems := make([][]byte, len(h.Codecs))
		for i, codec := range h.Codecs {
			codecItems[i] = packStr(codec)
		}
		fields = append(fields, mapField{"codecs", packArray(codecItems)})
	}
	return packMap(fields), nil
}

func DecodeHello(m map[string]interface{}) (*Hello, error) {
	roleV, err := reqField(m, "role")
	if err != nil {
		return nil, err
	}
	role, err := asEnum(roleV, helloRoles, "role")
	if err != nil {
		return nil, err
	}
	switch role {
	case "runtime":
		if err := noUnknown(m, setOf("role", "owner_id")); err != nil {
			return nil, err
		}
		ownerV, err := reqField(m, "owner_id")
		if err != nil {
			return nil, err
		}
		owner, err := asBin(ownerV, 16, "owner_id")
		if err != nil {
			return nil, err
		}
		return NewRuntimeHello(owner), nil
	case "worker":
		if err := noUnknown(m, setOf("role", "worker_id", "runtime", "runtime_version", "sdk_version", "codecs")); err != nil {
			return nil, err
		}
		workerV, err := reqField(m, "worker_id")
		if err != nil {
			return nil, err
		}
		worker, err := asStr(workerV, "worker_id")
		if err != nil {
			return nil, err
		}
		runtimeV, _ := reqField(m, "runtime")
		runtimeName, err := asEnum(runtimeV, workerRuntimes, "runtime")
		if err != nil {
			return nil, err
		}
		rv, _ := reqField(m, "runtime_version")
		runtimeVersion, err := asStr(rv, "runtime_version")
		if err != nil {
			return nil, err
		}
		sv, _ := reqField(m, "sdk_version")
		sdkVersion, err := asStr(sv, "sdk_version")
		if err != nil {
			return nil, err
		}
		cv, _ := reqField(m, "codecs")
		ca, err := asArray(cv, "codecs")
		if err != nil {
			return nil, err
		}
		codecs := make([]string, len(ca))
		for i, item := range ca {
			codecs[i], err = asStr(item, "codecs[]")
			if err != nil {
				return nil, err
			}
		}
		h := NewWorkerHello(worker, runtimeName, runtimeVersion, sdkVersion, codecs)
		if err := h.Validate(); err != nil {
			return nil, err
		}
		return h, nil
	default:
		if err := noUnknown(m, setOf("role")); err != nil {
			return nil, err
		}
		return NewAdminHello(), nil
	}
}

type PullRequest struct {
	WorkerID             string
	CapabilityGeneration uint64
}

func (p *PullRequest) Encode() []byte {
	return packMap([]mapField{
		{"worker_id", packStr(p.WorkerID)},
		{"capability_generation", packU64(p.CapabilityGeneration)},
	})
}

var pullRequestKeys = setOf("worker_id", "capability_generation")

func DecodePullRequest(m map[string]interface{}) (*PullRequest, error) {
	if err := noUnknown(m, pullRequestKeys); err != nil {
		return nil, err
	}
	workerV, err := reqField(m, "worker_id")
	if err != nil {
		return nil, err
	}
	worker, err := asStr(workerV, "worker_id")
	if err != nil {
		return nil, err
	}
	generationV, err := reqField(m, "capability_generation")
	if err != nil {
		return nil, err
	}
	generation, err := asUint(generationV, 64, "capability_generation")
	if err != nil {
		return nil, err
	}
	return &PullRequest{WorkerID: worker, CapabilityGeneration: generation}, nil
}

type TaskCapability struct {
	TaskName    string
	TaskVersion string
	Invocation  string
	Codecs      []string
}

func (t TaskCapability) Validate() error {
	if t.TaskName == "" || t.TaskVersion == "" || !invocationProfiles[t.Invocation] || len(t.Codecs) == 0 {
		return NewDecodeError(InvalidMessage, "invalid task capability")
	}
	seen := make(map[string]bool, len(t.Codecs))
	for _, codec := range t.Codecs {
		if codec == "" || seen[codec] {
			return NewDecodeError(InvalidMessage, "task capability codecs must be non-empty and unique")
		}
		seen[codec] = true
	}
	return nil
}

func (t TaskCapability) Encode() ([]byte, error) {
	if err := t.Validate(); err != nil {
		return nil, err
	}
	items := make([][]byte, len(t.Codecs))
	for i, v := range t.Codecs {
		items[i] = packStr(v)
	}
	return packMap([]mapField{{"task_name", packStr(t.TaskName)}, {"task_version", packStr(t.TaskVersion)}, {"invocation", packStr(t.Invocation)}, {"codecs", packArray(items)}}), nil
}
func DecodeTaskCapability(m map[string]interface{}) (TaskCapability, error) {
	if err := noUnknown(m, setOf("task_name", "task_version", "invocation", "codecs")); err != nil {
		return TaskCapability{}, err
	}
	nv, _ := reqField(m, "task_name")
	n, e := asStr(nv, "task_name")
	if e != nil {
		return TaskCapability{}, e
	}
	vv, _ := reqField(m, "task_version")
	v, e := asStr(vv, "task_version")
	if e != nil {
		return TaskCapability{}, e
	}
	iv, _ := reqField(m, "invocation")
	inv, e := asEnum(iv, invocationProfiles, "invocation")
	if e != nil {
		return TaskCapability{}, e
	}
	cv, _ := reqField(m, "codecs")
	ca, e := asArray(cv, "codecs")
	if e != nil {
		return TaskCapability{}, e
	}
	codecs := make([]string, len(ca))
	for i, x := range ca {
		codecs[i], e = asStr(x, "codecs[]")
		if e != nil {
			return TaskCapability{}, e
		}
	}
	if n == "" || v == "" || len(codecs) == 0 {
		return TaskCapability{}, NewDecodeError(InvalidMessage, "invalid task capability")
	}
	capability := TaskCapability{n, v, inv, codecs}
	if e := capability.Validate(); e != nil {
		return TaskCapability{}, e
	}
	return capability, nil
}

type TaskRegistration struct {
	WorkerID   string
	Generation uint64
	Tasks      []TaskCapability
}

func (t *TaskRegistration) Validate() error {
	if t == nil || t.WorkerID == "" || t.Generation == 0 {
		return NewDecodeError(InvalidMessage, "invalid task registration")
	}
	seen := map[string]bool{}
	for _, task := range t.Tasks {
		if err := task.Validate(); err != nil {
			return err
		}
		key := task.TaskName + "\x00" + task.TaskVersion
		if seen[key] {
			return NewDecodeError(InvalidMessage, "duplicate task capability")
		}
		seen[key] = true
	}
	return nil
}

func (t *TaskRegistration) Encode() ([]byte, error) {
	if err := t.Validate(); err != nil {
		return nil, err
	}
	items := make([][]byte, len(t.Tasks))
	for i := range t.Tasks {
		encoded, err := t.Tasks[i].Encode()
		if err != nil {
			return nil, err
		}
		items[i] = encoded
	}
	return packMap([]mapField{{"worker_id", packStr(t.WorkerID)}, {"generation", packU64(t.Generation)}, {"tasks", packArray(items)}}), nil
}

var taskRegistrationKeys = setOf("worker_id", "generation", "tasks")

func DecodeTaskRegistration(m map[string]interface{}) (*TaskRegistration, error) {
	if err := noUnknown(m, taskRegistrationKeys); err != nil {
		return nil, err
	}
	wv, _ := reqField(m, "worker_id")
	w, e := asStr(wv, "worker_id")
	if e != nil {
		return nil, e
	}
	gv, _ := reqField(m, "generation")
	g, e := asUint(gv, 64, "generation")
	if e != nil || g == 0 {
		if e != nil {
			return nil, e
		}
		return nil, NewDecodeError(InvalidMessage, "generation must be positive")
	}
	tv, _ := reqField(m, "tasks")
	ta, e := asArray(tv, "tasks")
	if e != nil {
		return nil, e
	}
	tasks := make([]TaskCapability, len(ta))
	seen := map[string]bool{}
	for i, x := range ta {
		mm, e := asMap(x, "tasks[]")
		if e != nil {
			return nil, e
		}
		tasks[i], e = DecodeTaskCapability(mm)
		if e != nil {
			return nil, e
		}
		key := tasks[i].TaskName + "\x00" + tasks[i].TaskVersion
		if seen[key] {
			return nil, NewDecodeError(InvalidMessage, "duplicate task capability")
		}
		seen[key] = true
	}
	registration := &TaskRegistration{w, g, tasks}
	if e := registration.Validate(); e != nil {
		return nil, e
	}
	return registration, nil
}

type TaskQuery struct {
	OwnerID []byte
	TaskIDs [][]byte
}

func (t *TaskQuery) Encode() []byte {
	ids := make([][]byte, len(t.TaskIDs))
	for i, id := range t.TaskIDs {
		ids[i] = packBin(id)
	}
	return packMap([]mapField{
		{"owner_id", packBin(t.OwnerID)},
		{"task_ids", packArray(ids)},
	})
}

var taskQueryKeys = setOf("owner_id", "task_ids")

func DecodeTaskQuery(m map[string]interface{}) (*TaskQuery, error) {
	if err := noUnknown(m, taskQueryKeys); err != nil {
		return nil, err
	}
	ownerV, err := reqField(m, "owner_id")
	if err != nil {
		return nil, err
	}
	owner, err := asBin(ownerV, 16, "owner_id")
	if err != nil {
		return nil, err
	}
	idsV, err := reqField(m, "task_ids")
	if err != nil {
		return nil, err
	}
	rawIDs, err := asArray(idsV, "task_ids")
	if err != nil {
		return nil, err
	}
	if len(rawIDs) < 1 {
		return nil, NewDecodeError(InvalidMessage, "task_ids must be non-empty")
	}
	ids := make([][]byte, len(rawIDs))
	for i, raw := range rawIDs {
		id, err := asBin(raw, 16, "task_ids[]")
		if err != nil {
			return nil, err
		}
		ids[i] = id
	}
	return &TaskQuery{OwnerID: owner, TaskIDs: ids}, nil
}

var taskStates = setOf("queued", "leased", "succeeded", "failed", "cancelled", "dead_lettered", "unknown")

type TaskSnapshotEntry struct {
	TaskID  []byte
	State   string
	Cursor  *uint64
	Result  *ObjectRef
	Failure *Failure
}

func (e *TaskSnapshotEntry) Encode() []byte {
	var cursorB, resultB, failureB []byte
	if e.Cursor != nil {
		cursorB = packU64(*e.Cursor)
	}
	if e.Result != nil {
		resultB = e.Result.Encode()
	}
	if e.Failure != nil {
		failureB = e.Failure.Encode()
	}
	return packMap([]mapField{
		{"task_id", packBin(e.TaskID)},
		{"state", packStr(e.State)},
		{"cursor", packOptional(cursorB, e.Cursor != nil)},
		{"result", packOptional(resultB, e.Result != nil)},
		{"failure", packOptional(failureB, e.Failure != nil)},
	})
}

var taskSnapshotEntryKeys = setOf("task_id", "state", "cursor", "result", "failure")

func DecodeTaskSnapshotEntry(m map[string]interface{}) (*TaskSnapshotEntry, error) {
	if err := noUnknown(m, taskSnapshotEntryKeys); err != nil {
		return nil, err
	}
	idV, err := reqField(m, "task_id")
	if err != nil {
		return nil, err
	}
	id, err := asBin(idV, 16, "task_id")
	if err != nil {
		return nil, err
	}
	stateV, err := reqField(m, "state")
	if err != nil {
		return nil, err
	}
	state, err := asEnum(stateV, taskStates, "state")
	if err != nil {
		return nil, err
	}
	cursorV, err := reqField(m, "cursor")
	if err != nil {
		return nil, err
	}
	cursor, err := decodeOptional(cursorV, func(v interface{}) (uint64, error) { return asUint(v, 64, "cursor") })
	if err != nil {
		return nil, err
	}
	resultV, err := reqField(m, "result")
	if err != nil {
		return nil, err
	}
	result, err := decodeOptional(resultV, func(v interface{}) (ObjectRef, error) {
		mm, err := asMap(v, "result")
		if err != nil {
			return ObjectRef{}, err
		}
		o, err := DecodeObjectRef(mm)
		if err != nil {
			return ObjectRef{}, err
		}
		return *o, nil
	})
	if err != nil {
		return nil, err
	}
	failureV, err := reqField(m, "failure")
	if err != nil {
		return nil, err
	}
	failure, err := decodeOptional(failureV, func(v interface{}) (Failure, error) {
		mm, err := asMap(v, "failure")
		if err != nil {
			return Failure{}, err
		}
		f, err := DecodeFailure(mm)
		if err != nil {
			return Failure{}, err
		}
		return *f, nil
	})
	if err != nil {
		return nil, err
	}
	return &TaskSnapshotEntry{TaskID: id, State: state, Cursor: cursor, Result: result, Failure: failure}, nil
}

type TaskSnapshot struct {
	Tasks []TaskSnapshotEntry
}

func (t *TaskSnapshot) Encode() []byte {
	items := make([][]byte, len(t.Tasks))
	for i := range t.Tasks {
		items[i] = t.Tasks[i].Encode()
	}
	return packMap([]mapField{{"tasks", packArray(items)}})
}

var taskSnapshotKeys = setOf("tasks")

func DecodeTaskSnapshot(m map[string]interface{}) (*TaskSnapshot, error) {
	if err := noUnknown(m, taskSnapshotKeys); err != nil {
		return nil, err
	}
	tasksV, err := reqField(m, "tasks")
	if err != nil {
		return nil, err
	}
	raw, err := asArray(tasksV, "tasks")
	if err != nil {
		return nil, err
	}
	entries := make([]TaskSnapshotEntry, len(raw))
	for i, r := range raw {
		mm, err := asMap(r, "tasks[]")
		if err != nil {
			return nil, err
		}
		e, err := DecodeTaskSnapshotEntry(mm)
		if err != nil {
			return nil, err
		}
		entries[i] = *e
	}
	return &TaskSnapshot{Tasks: entries}, nil
}

// --------------------------------------------------------------------
// Task and result envelopes
// --------------------------------------------------------------------

type TaskEnvelope struct {
	OwnerID           []byte
	TaskName          string
	TaskVersion       string
	Invocation        string
	Input             *ValueRef
	Labels            map[string]string
	Idempotent        bool
	SubmittedAtUnixMs int64
}

func (t *TaskEnvelope) Encode() ([]byte, error) {
	inputB, err := t.Input.Encode()
	if err != nil {
		return nil, err
	}
	return packMap([]mapField{
		{"owner_id", packBin(t.OwnerID)},
		{"task_name", packStr(t.TaskName)},
		{"task_version", packStr(t.TaskVersion)},
		{"invocation", packStr(t.Invocation)},
		{"input", inputB},
		{"labels", packStrMap(t.Labels)},
		{"idempotent", packBool(t.Idempotent)},
		{"submitted_at_unix_ms", packI64(t.SubmittedAtUnixMs)},
	}), nil
}

var taskEnvelopeKeys = setOf(
	"owner_id", "task_name", "task_version", "invocation", "input", "labels", "idempotent", "submitted_at_unix_ms",
)

func DecodeTaskEnvelope(m map[string]interface{}) (*TaskEnvelope, error) {
	if err := noUnknown(m, taskEnvelopeKeys); err != nil {
		return nil, err
	}
	ownerV, err := reqField(m, "owner_id")
	if err != nil {
		return nil, err
	}
	owner, err := asBin(ownerV, 16, "owner_id")
	if err != nil {
		return nil, err
	}
	nameV, err := reqField(m, "task_name")
	if err != nil {
		return nil, err
	}
	name, err := asStr(nameV, "task_name")
	if err != nil {
		return nil, err
	}
	versionV, err := reqField(m, "task_version")
	if err != nil {
		return nil, err
	}
	version, err := asStr(versionV, "task_version")
	if err != nil {
		return nil, err
	}
	invocationV, err := reqField(m, "invocation")
	if err != nil {
		return nil, err
	}
	invocation, err := asEnum(invocationV, invocationProfiles, "invocation")
	if err != nil {
		return nil, err
	}
	inputV, err := reqField(m, "input")
	if err != nil {
		return nil, err
	}
	inputMap, err := asMap(inputV, "input")
	if err != nil {
		return nil, err
	}
	input, err := DecodeValueRef(inputMap)
	if err != nil {
		return nil, err
	}
	labelsV, err := reqField(m, "labels")
	if err != nil {
		return nil, err
	}
	labels, err := asStrMap(labelsV, "labels")
	if err != nil {
		return nil, err
	}
	idempotentV, err := reqField(m, "idempotent")
	if err != nil {
		return nil, err
	}
	idempotent, err := asBool(idempotentV, "idempotent")
	if err != nil {
		return nil, err
	}
	submittedV, err := reqField(m, "submitted_at_unix_ms")
	if err != nil {
		return nil, err
	}
	submitted, err := asInt(submittedV, 64, "submitted_at_unix_ms")
	if err != nil {
		return nil, err
	}
	return &TaskEnvelope{
		OwnerID: owner, TaskName: name, TaskVersion: version, Invocation: invocation, Input: input,
		Labels: labels, Idempotent: idempotent, SubmittedAtUnixMs: submitted,
	}, nil
}

type LeasedTask struct {
	Task    *TaskEnvelope
	LeaseID []byte
	TTLMs   uint32
	Attempt uint32
}

func (l *LeasedTask) Encode() ([]byte, error) {
	taskB, err := l.Task.Encode()
	if err != nil {
		return nil, err
	}
	return packMap([]mapField{
		{"task", taskB},
		{"lease_id", packBin(l.LeaseID)},
		{"ttl_ms", packU32(l.TTLMs)},
		{"attempt", packU32(l.Attempt)},
	}), nil
}

var leasedTaskKeys = setOf("task", "lease_id", "ttl_ms", "attempt")

func DecodeLeasedTask(m map[string]interface{}) (*LeasedTask, error) {
	if err := noUnknown(m, leasedTaskKeys); err != nil {
		return nil, err
	}
	taskV, err := reqField(m, "task")
	if err != nil {
		return nil, err
	}
	taskMap, err := asMap(taskV, "task")
	if err != nil {
		return nil, err
	}
	task, err := DecodeTaskEnvelope(taskMap)
	if err != nil {
		return nil, err
	}
	leaseV, err := reqField(m, "lease_id")
	if err != nil {
		return nil, err
	}
	lease, err := asBin(leaseV, 16, "lease_id")
	if err != nil {
		return nil, err
	}
	ttlV, err := reqField(m, "ttl_ms")
	if err != nil {
		return nil, err
	}
	ttl, err := asUint(ttlV, 32, "ttl_ms")
	if err != nil {
		return nil, err
	}
	attemptV, err := reqField(m, "attempt")
	if err != nil {
		return nil, err
	}
	attempt, err := asUint(attemptV, 32, "attempt")
	if err != nil {
		return nil, err
	}
	return &LeasedTask{Task: task, LeaseID: lease, TTLMs: uint32(ttl), Attempt: uint32(attempt)}, nil
}

func validateResultXorFailure(result *ObjectRef, failure *Failure) error {
	if (result == nil) == (failure == nil) {
		return NewDecodeError(InvalidMessage, "exactly one of result or failure is required")
	}
	return nil
}

type Completion struct {
	LeaseID []byte
	Result  *ObjectRef
	Failure *Failure
}

func (c *Completion) Encode() ([]byte, error) {
	if err := validateResultXorFailure(c.Result, c.Failure); err != nil {
		return nil, err
	}
	var resultB, failureB []byte
	if c.Result != nil {
		resultB = c.Result.Encode()
	}
	if c.Failure != nil {
		failureB = c.Failure.Encode()
	}
	return packMap([]mapField{
		{"lease_id", packBin(c.LeaseID)},
		{"result", packOptional(resultB, c.Result != nil)},
		{"failure", packOptional(failureB, c.Failure != nil)},
	}), nil
}

var completionKeys = setOf("lease_id", "result", "failure")

func decodeOptionalObjectRef(v interface{}, field string) (*ObjectRef, error) {
	return decodeOptional(v, func(x interface{}) (ObjectRef, error) {
		mm, err := asMap(x, field)
		if err != nil {
			return ObjectRef{}, err
		}
		o, err := DecodeObjectRef(mm)
		if err != nil {
			return ObjectRef{}, err
		}
		return *o, nil
	})
}

func decodeOptionalFailure(v interface{}, field string) (*Failure, error) {
	return decodeOptional(v, func(x interface{}) (Failure, error) {
		mm, err := asMap(x, field)
		if err != nil {
			return Failure{}, err
		}
		f, err := DecodeFailure(mm)
		if err != nil {
			return Failure{}, err
		}
		return *f, nil
	})
}

func DecodeCompletion(m map[string]interface{}) (*Completion, error) {
	if err := noUnknown(m, completionKeys); err != nil {
		return nil, err
	}
	leaseV, err := reqField(m, "lease_id")
	if err != nil {
		return nil, err
	}
	lease, err := asBin(leaseV, 16, "lease_id")
	if err != nil {
		return nil, err
	}
	resultV, err := reqField(m, "result")
	if err != nil {
		return nil, err
	}
	result, err := decodeOptionalObjectRef(resultV, "result")
	if err != nil {
		return nil, err
	}
	failureV, err := reqField(m, "failure")
	if err != nil {
		return nil, err
	}
	failure, err := decodeOptionalFailure(failureV, "failure")
	if err != nil {
		return nil, err
	}
	if err := validateResultXorFailure(result, failure); err != nil {
		return nil, err
	}
	return &Completion{LeaseID: lease, Result: result, Failure: failure}, nil
}

type ForwardedTask struct {
	TransferID []byte
	OriginNode string
	Task       *TaskEnvelope
}

func (f *ForwardedTask) Encode() ([]byte, error) {
	taskB, err := f.Task.Encode()
	if err != nil {
		return nil, err
	}
	return packMap([]mapField{
		{"transfer_id", packBin(f.TransferID)},
		{"origin_node", packStr(f.OriginNode)},
		{"task", taskB},
	}), nil
}

var forwardedTaskKeys = setOf("transfer_id", "origin_node", "task")

func DecodeForwardedTask(m map[string]interface{}) (*ForwardedTask, error) {
	if err := noUnknown(m, forwardedTaskKeys); err != nil {
		return nil, err
	}
	transferV, err := reqField(m, "transfer_id")
	if err != nil {
		return nil, err
	}
	transfer, err := asBin(transferV, 16, "transfer_id")
	if err != nil {
		return nil, err
	}
	originV, err := reqField(m, "origin_node")
	if err != nil {
		return nil, err
	}
	origin, err := asStr(originV, "origin_node")
	if err != nil {
		return nil, err
	}
	taskV, err := reqField(m, "task")
	if err != nil {
		return nil, err
	}
	taskMap, err := asMap(taskV, "task")
	if err != nil {
		return nil, err
	}
	task, err := DecodeTaskEnvelope(taskMap)
	if err != nil {
		return nil, err
	}
	return &ForwardedTask{TransferID: transfer, OriginNode: origin, Task: task}, nil
}

type ForwardedCompletion struct {
	TransferID    []byte
	RemoteNode    string
	RemoteAttempt uint32
	Result        *ObjectRef
	Failure       *Failure
}

func (f *ForwardedCompletion) Encode() ([]byte, error) {
	if err := validateResultXorFailure(f.Result, f.Failure); err != nil {
		return nil, err
	}
	var resultB, failureB []byte
	if f.Result != nil {
		resultB = f.Result.Encode()
	}
	if f.Failure != nil {
		failureB = f.Failure.Encode()
	}
	return packMap([]mapField{
		{"transfer_id", packBin(f.TransferID)},
		{"remote_node", packStr(f.RemoteNode)},
		{"remote_attempt", packU32(f.RemoteAttempt)},
		{"result", packOptional(resultB, f.Result != nil)},
		{"failure", packOptional(failureB, f.Failure != nil)},
	}), nil
}

var forwardedCompletionKeys = setOf("transfer_id", "remote_node", "remote_attempt", "result", "failure")

func DecodeForwardedCompletion(m map[string]interface{}) (*ForwardedCompletion, error) {
	if err := noUnknown(m, forwardedCompletionKeys); err != nil {
		return nil, err
	}
	transferV, err := reqField(m, "transfer_id")
	if err != nil {
		return nil, err
	}
	transfer, err := asBin(transferV, 16, "transfer_id")
	if err != nil {
		return nil, err
	}
	nodeV, err := reqField(m, "remote_node")
	if err != nil {
		return nil, err
	}
	node, err := asStr(nodeV, "remote_node")
	if err != nil {
		return nil, err
	}
	attemptV, err := reqField(m, "remote_attempt")
	if err != nil {
		return nil, err
	}
	attempt, err := asUint(attemptV, 32, "remote_attempt")
	if err != nil {
		return nil, err
	}
	resultV, err := reqField(m, "result")
	if err != nil {
		return nil, err
	}
	result, err := decodeOptionalObjectRef(resultV, "result")
	if err != nil {
		return nil, err
	}
	failureV, err := reqField(m, "failure")
	if err != nil {
		return nil, err
	}
	failure, err := decodeOptionalFailure(failureV, "failure")
	if err != nil {
		return nil, err
	}
	if err := validateResultXorFailure(result, failure); err != nil {
		return nil, err
	}
	return &ForwardedCompletion{
		TransferID: transfer, RemoteNode: node, RemoteAttempt: uint32(attempt),
		Result: result, Failure: failure,
	}, nil
}

type Failure struct {
	Code      string
	Message   string
	Details   *ValueRef
	Retryable bool
}

func (f *Failure) Encode() []byte {
	var detailsB []byte
	if f.Details != nil {
		// Details validity was already checked at construction time via
		// DecodeValueRef/NewInlineValueRef/NewObjectValueRef.
		b, _ := f.Details.Encode()
		detailsB = b
	}
	return packMap([]mapField{
		{"code", packStr(f.Code)},
		{"message", packStr(f.Message)},
		{"details", packOptional(detailsB, f.Details != nil)},
		{"retryable", packBool(f.Retryable)},
	})
}

var failureKeys = setOf("code", "message", "details", "retryable")

func DecodeFailure(m map[string]interface{}) (*Failure, error) {
	if err := noUnknown(m, failureKeys); err != nil {
		return nil, err
	}
	codeV, err := reqField(m, "code")
	if err != nil {
		return nil, err
	}
	code, err := asStr(codeV, "code")
	if err != nil {
		return nil, err
	}
	msgV, err := reqField(m, "message")
	if err != nil {
		return nil, err
	}
	msg, err := asStr(msgV, "message")
	if err != nil {
		return nil, err
	}
	detailsV, err := reqField(m, "details")
	if err != nil {
		return nil, err
	}
	details, err := decodeOptional(detailsV, func(v interface{}) (ValueRef, error) {
		mm, err := asMap(v, "details")
		if err != nil {
			return ValueRef{}, err
		}
		vr, err := DecodeValueRef(mm)
		if err != nil {
			return ValueRef{}, err
		}
		return *vr, nil
	})
	if err != nil {
		return nil, err
	}
	retryableV, err := reqField(m, "retryable")
	if err != nil {
		return nil, err
	}
	retryable, err := asBool(retryableV, "retryable")
	if err != nil {
		return nil, err
	}
	return &Failure{Code: code, Message: msg, Details: details, Retryable: retryable}, nil
}

var resultStates = setOf("succeeded", "failed", "cancelled")

type ResultNotification struct {
	OwnerID []byte
	Cursor  uint64
	State   string
	Result  *ObjectRef
	Failure *Failure
}

func (r *ResultNotification) Encode() []byte {
	var resultB, failureB []byte
	if r.Result != nil {
		resultB = r.Result.Encode()
	}
	if r.Failure != nil {
		failureB = r.Failure.Encode()
	}
	return packMap([]mapField{
		{"owner_id", packBin(r.OwnerID)},
		{"cursor", packU64(r.Cursor)},
		{"state", packStr(r.State)},
		{"result", packOptional(resultB, r.Result != nil)},
		{"failure", packOptional(failureB, r.Failure != nil)},
	})
}

var resultNotificationKeys = setOf("owner_id", "cursor", "state", "result", "failure")

func DecodeResultNotification(m map[string]interface{}) (*ResultNotification, error) {
	if err := noUnknown(m, resultNotificationKeys); err != nil {
		return nil, err
	}
	ownerV, err := reqField(m, "owner_id")
	if err != nil {
		return nil, err
	}
	owner, err := asBin(ownerV, 16, "owner_id")
	if err != nil {
		return nil, err
	}
	cursorV, err := reqField(m, "cursor")
	if err != nil {
		return nil, err
	}
	cursor, err := asUint(cursorV, 64, "cursor")
	if err != nil {
		return nil, err
	}
	stateV, err := reqField(m, "state")
	if err != nil {
		return nil, err
	}
	state, err := asEnum(stateV, resultStates, "state")
	if err != nil {
		return nil, err
	}
	resultV, err := reqField(m, "result")
	if err != nil {
		return nil, err
	}
	result, err := decodeOptionalObjectRef(resultV, "result")
	if err != nil {
		return nil, err
	}
	failureV, err := reqField(m, "failure")
	if err != nil {
		return nil, err
	}
	failure, err := decodeOptionalFailure(failureV, "failure")
	if err != nil {
		return nil, err
	}
	return &ResultNotification{OwnerID: owner, Cursor: cursor, State: state, Result: result, Failure: failure}, nil
}

type StatusSnapshot struct {
	Version            string
	PID                uint64
	Ready              bool
	TaskCounts         map[string]uint64
	ActiveLeases       uint64
	WorkerPIDs         []uint64
	WorkerRestarts     uint64
	StorageHealthy     bool
	ClusterMembers     uint64
	KafkaOutboxPending uint64
	LastErrorCode      *string
}

func (s *StatusSnapshot) Encode() []byte {
	pids := make([][]byte, len(s.WorkerPIDs))
	for i, p := range s.WorkerPIDs {
		pids[i] = packU64(p)
	}
	var lastErrB []byte
	if s.LastErrorCode != nil {
		lastErrB = packStr(*s.LastErrorCode)
	}
	return packMap([]mapField{
		{"version", packStr(s.Version)},
		{"pid", packU64(s.PID)},
		{"ready", packBool(s.Ready)},
		{"task_counts", packStrU64Map(s.TaskCounts)},
		{"active_leases", packU64(s.ActiveLeases)},
		{"worker_pids", packArray(pids)},
		{"worker_restarts", packU64(s.WorkerRestarts)},
		{"storage_healthy", packBool(s.StorageHealthy)},
		{"cluster_members", packU64(s.ClusterMembers)},
		{"kafka_outbox_pending", packU64(s.KafkaOutboxPending)},
		{"last_error_code", packOptional(lastErrB, s.LastErrorCode != nil)},
	})
}

var statusSnapshotKeys = setOf(
	"version", "pid", "ready", "task_counts", "active_leases", "worker_pids",
	"worker_restarts", "storage_healthy", "cluster_members", "kafka_outbox_pending", "last_error_code",
)

// DecodeStatusSnapshot is forward-compatible: unknown keys are ignored.
func DecodeStatusSnapshot(m map[string]interface{}) (*StatusSnapshot, error) {
	versionV, err := reqField(m, "version")
	if err != nil {
		return nil, err
	}
	version, err := asStr(versionV, "version")
	if err != nil {
		return nil, err
	}
	pidV, err := reqField(m, "pid")
	if err != nil {
		return nil, err
	}
	pid, err := asUint(pidV, 64, "pid")
	if err != nil {
		return nil, err
	}
	readyV, err := reqField(m, "ready")
	if err != nil {
		return nil, err
	}
	ready, err := asBool(readyV, "ready")
	if err != nil {
		return nil, err
	}
	countsV, err := reqField(m, "task_counts")
	if err != nil {
		return nil, err
	}
	countsMap, err := asMap(countsV, "task_counts")
	if err != nil {
		return nil, err
	}
	counts := make(map[string]uint64, len(countsMap))
	for k, v := range countsMap {
		u, err := asUint(v, 64, "task_counts value")
		if err != nil {
			return nil, err
		}
		counts[k] = u
	}
	activeV, err := reqField(m, "active_leases")
	if err != nil {
		return nil, err
	}
	active, err := asUint(activeV, 64, "active_leases")
	if err != nil {
		return nil, err
	}
	pidsV, err := reqField(m, "worker_pids")
	if err != nil {
		return nil, err
	}
	rawPids, err := asArray(pidsV, "worker_pids")
	if err != nil {
		return nil, err
	}
	pids := make([]uint64, len(rawPids))
	for i, r := range rawPids {
		u, err := asUint(r, 64, "worker_pids[]")
		if err != nil {
			return nil, err
		}
		pids[i] = u
	}
	restartsV, err := reqField(m, "worker_restarts")
	if err != nil {
		return nil, err
	}
	restarts, err := asUint(restartsV, 64, "worker_restarts")
	if err != nil {
		return nil, err
	}
	healthyV, err := reqField(m, "storage_healthy")
	if err != nil {
		return nil, err
	}
	healthy, err := asBool(healthyV, "storage_healthy")
	if err != nil {
		return nil, err
	}
	membersV, err := reqField(m, "cluster_members")
	if err != nil {
		return nil, err
	}
	members, err := asUint(membersV, 64, "cluster_members")
	if err != nil {
		return nil, err
	}
	kafkaV, err := reqField(m, "kafka_outbox_pending")
	if err != nil {
		return nil, err
	}
	kafka, err := asUint(kafkaV, 64, "kafka_outbox_pending")
	if err != nil {
		return nil, err
	}
	lastErrV, err := reqField(m, "last_error_code")
	if err != nil {
		return nil, err
	}
	lastErr, err := decodeOptional(lastErrV, func(v interface{}) (string, error) { return asStr(v, "last_error_code") })
	if err != nil {
		return nil, err
	}
	return &StatusSnapshot{
		Version: version, PID: pid, Ready: ready, TaskCounts: counts, ActiveLeases: active,
		WorkerPIDs: pids, WorkerRestarts: restarts, StorageHealthy: healthy, ClusterMembers: members,
		KafkaOutboxPending: kafka, LastErrorCode: lastErr,
	}, nil
}

// --------------------------------------------------------------------
// Ack / Error
// --------------------------------------------------------------------

var ackKindFields = map[string][]string{
	"hello":          {},
	"submit":         {"task_id"},
	"forward":        {"task_id", "transfer_id"},
	"heartbeat":      {"lease_id"},
	"complete":       {"lease_id"},
	"cancel":         {"task_id", "cancelled"},
	"result":         {"owner_id", "task_id", "cursor"},
	"empty_pull":     {},
	"object_put":     {"transfer_id", "object"},
	"object_get":     {"transfer_id"},
	"resume":         {"owner_id", "next_cursor", "more"},
	"steal":          {"transfer_id", "accepted"},
	"register_tasks": {"worker_id", "generation", "accepted"},
}

// Ack is a typed union over the twelve response kinds. Fields holds only
// the keys the kind requires; NewAck validates that set exactly.
type Ack struct {
	Kind   string
	Fields map[string]interface{}
}

func NewAck(kind string, fields map[string]interface{}) (*Ack, error) {
	a := &Ack{Kind: kind, Fields: fields}
	if err := a.Validate(); err != nil {
		return nil, err
	}
	return a, nil
}

func (a *Ack) Validate() error {
	required, ok := ackKindFields[a.Kind]
	if !ok {
		return NewDecodeError(InvalidMessage, "unknown Ack kind "+a.Kind)
	}
	want := setOf(required...)
	if len(want) != len(a.Fields) {
		return NewDecodeError(InvalidMessage, "Ack(kind="+a.Kind+") has wrong field set")
	}
	for k := range want {
		if _, ok := a.Fields[k]; !ok {
			return NewDecodeError(InvalidMessage, "Ack(kind="+a.Kind+") missing field "+k)
		}
	}
	return nil
}

func (a *Ack) Encode() ([]byte, error) {
	if err := a.Validate(); err != nil {
		return nil, err
	}
	fields := []mapField{{"kind", packStr(a.Kind)}}
	for name, value := range a.Fields {
		fields = append(fields, mapField{name, encodeAckField(name, value)})
	}
	return packMap(fields), nil
}

func encodeAckField(name string, value interface{}) []byte {
	switch name {
	case "task_id", "transfer_id", "lease_id", "owner_id":
		return packBin(value.([]byte))
	case "cancelled", "more":
		return packBool(value.(bool))
	case "cursor", "next_cursor":
		return packU64(value.(uint64))
	case "generation":
		return packU64(value.(uint64))
	case "worker_id":
		return packStr(value.(string))
	case "accepted":
		return packU32(value.(uint32))
	case "object":
		return value.(*ObjectRef).Encode()
	default:
		panic("protocol: unknown Ack field " + name)
	}
}

func decodeAckField(name string, v interface{}) (interface{}, error) {
	switch name {
	case "task_id", "transfer_id", "lease_id", "owner_id":
		return asBin(v, 16, name)
	case "cancelled", "more":
		return asBool(v, name)
	case "cursor", "next_cursor":
		return asUint(v, 64, name)
	case "generation":
		return asUint(v, 64, name)
	case "worker_id":
		return asStr(v, name)
	case "accepted":
		u, err := asUint(v, 32, name)
		if err != nil {
			return nil, err
		}
		return uint32(u), nil
	case "object":
		mm, err := asMap(v, name)
		if err != nil {
			return nil, err
		}
		return DecodeObjectRef(mm)
	default:
		return nil, NewDecodeError(InvalidMessage, "unknown Ack field "+name)
	}
}

func DecodeAck(m map[string]interface{}) (*Ack, error) {
	kindV, err := reqField(m, "kind")
	if err != nil {
		return nil, err
	}
	kind, err := asStr(kindV, "kind")
	if err != nil {
		return nil, err
	}
	required, ok := ackKindFields[kind]
	if !ok {
		return nil, NewDecodeError(InvalidMessage, "unknown Ack kind "+kind)
	}
	allowed := setOf(append(append([]string{}, required...), "kind")...)
	if err := noUnknown(m, allowed); err != nil {
		return nil, err
	}
	fields := make(map[string]interface{}, len(required))
	for _, name := range required {
		v, err := reqField(m, name)
		if err != nil {
			return nil, err
		}
		decoded, err := decodeAckField(name, v)
		if err != nil {
			return nil, err
		}
		fields[name] = decoded
	}
	return &Ack{Kind: kind, Fields: fields}, nil
}

type Error struct {
	Code      string
	Message   string
	Retryable bool
	Details   map[string]string
}

func (e *Error) Encode() []byte {
	return packMap([]mapField{
		{"code", packStr(e.Code)},
		{"message", packStr(e.Message)},
		{"retryable", packBool(e.Retryable)},
		{"details", packStrMap(e.Details)},
	})
}

var errorKeys = setOf("code", "message", "retryable", "details")

func DecodeErrorPayload(m map[string]interface{}) (*Error, error) {
	if err := noUnknown(m, errorKeys); err != nil {
		return nil, err
	}
	codeV, err := reqField(m, "code")
	if err != nil {
		return nil, err
	}
	code, err := asStr(codeV, "code")
	if err != nil {
		return nil, err
	}
	msgV, err := reqField(m, "message")
	if err != nil {
		return nil, err
	}
	msg, err := asStr(msgV, "message")
	if err != nil {
		return nil, err
	}
	retryableV, err := reqField(m, "retryable")
	if err != nil {
		return nil, err
	}
	retryable, err := asBool(retryableV, "retryable")
	if err != nil {
		return nil, err
	}
	detailsV, err := reqField(m, "details")
	if err != nil {
		return nil, err
	}
	details, err := asStrMap(detailsV, "details")
	if err != nil {
		return nil, err
	}
	return &Error{Code: code, Message: msg, Retryable: retryable, Details: details}, nil
}

// --------------------------------------------------------------------
// Remaining small request payloads
// --------------------------------------------------------------------

type HeartbeatRequest struct{ LeaseID []byte }

func (h *HeartbeatRequest) Encode() []byte {
	return packMap([]mapField{{"lease_id", packBin(h.LeaseID)}})
}

func DecodeHeartbeatRequest(m map[string]interface{}) (*HeartbeatRequest, error) {
	if err := noUnknown(m, setOf("lease_id")); err != nil {
		return nil, err
	}
	v, err := reqField(m, "lease_id")
	if err != nil {
		return nil, err
	}
	id, err := asBin(v, 16, "lease_id")
	if err != nil {
		return nil, err
	}
	return &HeartbeatRequest{LeaseID: id}, nil
}

type CancelRequest struct{ OwnerID []byte }

func (c *CancelRequest) Encode() []byte {
	return packMap([]mapField{{"owner_id", packBin(c.OwnerID)}})
}

func DecodeCancelRequest(m map[string]interface{}) (*CancelRequest, error) {
	if err := noUnknown(m, setOf("owner_id")); err != nil {
		return nil, err
	}
	v, err := reqField(m, "owner_id")
	if err != nil {
		return nil, err
	}
	id, err := asBin(v, 16, "owner_id")
	if err != nil {
		return nil, err
	}
	return &CancelRequest{OwnerID: id}, nil
}

type ResumeResultsRequest struct {
	OwnerID     []byte
	AfterCursor uint64
	Limit       uint32
}

func (r *ResumeResultsRequest) Encode() []byte {
	return packMap([]mapField{
		{"owner_id", packBin(r.OwnerID)},
		{"after_cursor", packU64(r.AfterCursor)},
		{"limit", packU32(r.Limit)},
	})
}

var resumeResultsKeys = setOf("owner_id", "after_cursor", "limit")

func DecodeResumeResultsRequest(m map[string]interface{}) (*ResumeResultsRequest, error) {
	if err := noUnknown(m, resumeResultsKeys); err != nil {
		return nil, err
	}
	ownerV, err := reqField(m, "owner_id")
	if err != nil {
		return nil, err
	}
	owner, err := asBin(ownerV, 16, "owner_id")
	if err != nil {
		return nil, err
	}
	afterV, err := reqField(m, "after_cursor")
	if err != nil {
		return nil, err
	}
	after, err := asUint(afterV, 64, "after_cursor")
	if err != nil {
		return nil, err
	}
	limitV, err := reqField(m, "limit")
	if err != nil {
		return nil, err
	}
	limit, err := asUint(limitV, 32, "limit")
	if err != nil {
		return nil, err
	}
	return &ResumeResultsRequest{OwnerID: owner, AfterCursor: after, Limit: uint32(limit)}, nil
}

type ObjectPutRequest struct {
	TransferID []byte
	Codec      string
	Size       uint64
	SHA256     []byte
}

func (o *ObjectPutRequest) Encode() []byte {
	return packMap([]mapField{
		{"transfer_id", packBin(o.TransferID)},
		{"codec", packStr(o.Codec)},
		{"size", packU64(o.Size)},
		{"sha256", packBin(o.SHA256)},
	})
}

var objectPutKeys = setOf("transfer_id", "codec", "size", "sha256")

func DecodeObjectPutRequest(m map[string]interface{}) (*ObjectPutRequest, error) {
	if err := noUnknown(m, objectPutKeys); err != nil {
		return nil, err
	}
	transferV, err := reqField(m, "transfer_id")
	if err != nil {
		return nil, err
	}
	transfer, err := asBin(transferV, 16, "transfer_id")
	if err != nil {
		return nil, err
	}
	codecV, err := reqField(m, "codec")
	if err != nil {
		return nil, err
	}
	codec, err := asStr(codecV, "codec")
	if err != nil {
		return nil, err
	}
	sizeV, err := reqField(m, "size")
	if err != nil {
		return nil, err
	}
	size, err := asUint(sizeV, 64, "size")
	if err != nil {
		return nil, err
	}
	shaV, err := reqField(m, "sha256")
	if err != nil {
		return nil, err
	}
	sha, err := asBin(shaV, 32, "sha256")
	if err != nil {
		return nil, err
	}
	return &ObjectPutRequest{TransferID: transfer, Codec: codec, Size: size, SHA256: sha}, nil
}

type ObjectGetRequest struct {
	TransferID []byte
	Object     *ObjectRef
}

func (o *ObjectGetRequest) Encode() []byte {
	return packMap([]mapField{
		{"transfer_id", packBin(o.TransferID)},
		{"object", o.Object.Encode()},
	})
}

var objectGetKeys = setOf("transfer_id", "object")

func DecodeObjectGetRequest(m map[string]interface{}) (*ObjectGetRequest, error) {
	if err := noUnknown(m, objectGetKeys); err != nil {
		return nil, err
	}
	transferV, err := reqField(m, "transfer_id")
	if err != nil {
		return nil, err
	}
	transfer, err := asBin(transferV, 16, "transfer_id")
	if err != nil {
		return nil, err
	}
	objV, err := reqField(m, "object")
	if err != nil {
		return nil, err
	}
	objMap, err := asMap(objV, "object")
	if err != nil {
		return nil, err
	}
	obj, err := DecodeObjectRef(objMap)
	if err != nil {
		return nil, err
	}
	return &ObjectGetRequest{TransferID: transfer, Object: obj}, nil
}

type ObjectChunk struct {
	TransferID []byte
	Sequence   uint64
	Data       []byte
	EOF        bool
}

func (o *ObjectChunk) Encode() []byte {
	return packMap([]mapField{
		{"transfer_id", packBin(o.TransferID)},
		{"sequence", packU64(o.Sequence)},
		{"data", packBin(o.Data)},
		{"eof", packBool(o.EOF)},
	})
}

var objectChunkKeys = setOf("transfer_id", "sequence", "data", "eof")

func DecodeObjectChunk(m map[string]interface{}) (*ObjectChunk, error) {
	if err := noUnknown(m, objectChunkKeys); err != nil {
		return nil, err
	}
	transferV, err := reqField(m, "transfer_id")
	if err != nil {
		return nil, err
	}
	transfer, err := asBin(transferV, 16, "transfer_id")
	if err != nil {
		return nil, err
	}
	seqV, err := reqField(m, "sequence")
	if err != nil {
		return nil, err
	}
	seq, err := asUint(seqV, 64, "sequence")
	if err != nil {
		return nil, err
	}
	dataV, err := reqField(m, "data")
	if err != nil {
		return nil, err
	}
	data, err := asBinAny(dataV, "data")
	if err != nil {
		return nil, err
	}
	eofV, err := reqField(m, "eof")
	if err != nil {
		return nil, err
	}
	eof, err := asBool(eofV, "eof")
	if err != nil {
		return nil, err
	}
	return &ObjectChunk{TransferID: transfer, Sequence: seq, Data: data, EOF: eof}, nil
}

type StealRequest struct {
	RequesterNode string
	Labels        map[string]string
	Limit         uint32
}

func (s *StealRequest) Encode() []byte {
	return packMap([]mapField{
		{"requester_node", packStr(s.RequesterNode)},
		{"labels", packStrMap(s.Labels)},
		{"limit", packU32(s.Limit)},
	})
}

var stealRequestKeys = setOf("requester_node", "labels", "limit")

func DecodeStealRequest(m map[string]interface{}) (*StealRequest, error) {
	if err := noUnknown(m, stealRequestKeys); err != nil {
		return nil, err
	}
	nodeV, err := reqField(m, "requester_node")
	if err != nil {
		return nil, err
	}
	node, err := asStr(nodeV, "requester_node")
	if err != nil {
		return nil, err
	}
	labelsV, err := reqField(m, "labels")
	if err != nil {
		return nil, err
	}
	labels, err := asStrMap(labelsV, "labels")
	if err != nil {
		return nil, err
	}
	limitV, err := reqField(m, "limit")
	if err != nil {
		return nil, err
	}
	limit, err := asUint(limitV, 32, "limit")
	if err != nil {
		return nil, err
	}
	return &StealRequest{RequesterNode: node, Labels: labels, Limit: uint32(limit)}, nil
}

type StatusRequest struct{}

func (StatusRequest) Encode() []byte { return packMap(nil) }

func DecodeStatusRequest(m map[string]interface{}) (*StatusRequest, error) {
	if err := noUnknown(m, setOf()); err != nil {
		return nil, err
	}
	return &StatusRequest{}, nil
}

// --------------------------------------------------------------------
// EncodePayload / DecodePayload dispatch
// --------------------------------------------------------------------

// EncodePayload renders value's canonical bytes for messageType, or
// returns an InvalidMessage error if value's type is not valid for it.
func EncodePayload(messageType MessageType, value interface{}) ([]byte, error) {
	payload, err := encodePayloadUnchecked(messageType, value)
	if err != nil {
		return nil, err
	}
	if _, err := DecodePayload(messageType, payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func encodePayloadUnchecked(messageType MessageType, value interface{}) ([]byte, error) {
	switch messageType {
	case MessageSubmit:
		switch v := value.(type) {
		case *TaskEnvelope:
			return v.Encode()
		case *ForwardedTask:
			return v.Encode()
		}
	case MessagePull:
		if v, ok := value.(*PullRequest); ok {
			return v.Encode(), nil
		}
	case MessageTask:
		if v, ok := value.(*LeasedTask); ok {
			return v.Encode()
		}
	case MessageHeartbeat:
		if v, ok := value.(*HeartbeatRequest); ok {
			return v.Encode(), nil
		}
	case MessageResult:
		if v, ok := value.(*ResultNotification); ok {
			return v.Encode(), nil
		}
	case MessageCancel:
		if v, ok := value.(*CancelRequest); ok {
			return v.Encode(), nil
		}
	case MessageComplete:
		switch v := value.(type) {
		case *Completion:
			return v.Encode()
		case *ForwardedCompletion:
			return v.Encode()
		}
	case MessageSteal:
		if v, ok := value.(*StealRequest); ok {
			return v.Encode(), nil
		}
	case MessageAck:
		if v, ok := value.(*Ack); ok {
			return v.Encode()
		}
	case MessageStatus:
		switch v := value.(type) {
		case *StatusRequest:
			return v.Encode(), nil
		case *StatusSnapshot:
			return v.Encode(), nil
		}
	case MessageResumeResults:
		if v, ok := value.(*ResumeResultsRequest); ok {
			return v.Encode(), nil
		}
	case MessageError:
		if v, ok := value.(*Error); ok {
			return v.Encode(), nil
		}
	case MessageObjectPut:
		if v, ok := value.(*ObjectPutRequest); ok {
			return v.Encode(), nil
		}
	case MessageObjectGet:
		if v, ok := value.(*ObjectGetRequest); ok {
			return v.Encode(), nil
		}
	case MessageObjectChunk:
		if v, ok := value.(*ObjectChunk); ok {
			return v.Encode(), nil
		}
	case MessageHello:
		if v, ok := value.(*Hello); ok {
			return v.Encode()
		}
	case MessageTaskQuery:
		switch v := value.(type) {
		case *TaskQuery:
			return v.Encode(), nil
		case *TaskSnapshot:
			return v.Encode(), nil
		}
	case MessageRegisterTasks:
		if v, ok := value.(*TaskRegistration); ok {
			return v.Encode()
		}
	}
	return nil, NewDecodeError(InvalidMessage, "value type is not valid for "+messageType.String())
}

// DecodePayload decodes payload according to messageType's schema (or
// union of schemas), disambiguating unions by their distinct key sets.
func DecodePayload(messageType MessageType, payload []byte) (interface{}, error) {
	raw, err := unpackStrict(payload)
	if err != nil {
		return nil, err
	}
	m, err := asMap(raw, "payload")
	if err != nil {
		return nil, err
	}

	switch messageType {
	case MessageSubmit:
		if keySetEquals(m, taskEnvelopeKeys) {
			return DecodeTaskEnvelope(m)
		}
		if keySetEquals(m, forwardedTaskKeys) {
			return DecodeForwardedTask(m)
		}
		return nil, NewDecodeError(InvalidMessage, "payload does not match SUBMIT variant")
	case MessagePull:
		return DecodePullRequest(m)
	case MessageTask:
		return DecodeLeasedTask(m)
	case MessageHeartbeat:
		return DecodeHeartbeatRequest(m)
	case MessageResult:
		return DecodeResultNotification(m)
	case MessageCancel:
		return DecodeCancelRequest(m)
	case MessageComplete:
		if keySetEquals(m, completionKeys) {
			return DecodeCompletion(m)
		}
		if keySetEquals(m, forwardedCompletionKeys) {
			return DecodeForwardedCompletion(m)
		}
		return nil, NewDecodeError(InvalidMessage, "payload does not match COMPLETE variant")
	case MessageSteal:
		return DecodeStealRequest(m)
	case MessageAck:
		return DecodeAck(m)
	case MessageStatus:
		if len(m) == 0 {
			return DecodeStatusRequest(m)
		}
		if keySetSuperset(m, statusSnapshotKeys) {
			return DecodeStatusSnapshot(m)
		}
		return nil, NewDecodeError(InvalidMessage, "payload does not match STATUS request or response")
	case MessageResumeResults:
		return DecodeResumeResultsRequest(m)
	case MessageError:
		return DecodeErrorPayload(m)
	case MessageObjectPut:
		return DecodeObjectPutRequest(m)
	case MessageObjectGet:
		return DecodeObjectGetRequest(m)
	case MessageObjectChunk:
		return DecodeObjectChunk(m)
	case MessageHello:
		return DecodeHello(m)
	case MessageTaskQuery:
		if keySetEquals(m, taskQueryKeys) {
			return DecodeTaskQuery(m)
		}
		if keySetEquals(m, taskSnapshotKeys) {
			return DecodeTaskSnapshot(m)
		}
		return nil, NewDecodeError(InvalidMessage, "payload does not match TASK_QUERY variant")
	case MessageRegisterTasks:
		return DecodeTaskRegistration(m)
	}
	return nil, NewDecodeError(InvalidMessage, "no decoder registered for "+messageType.String())
}
