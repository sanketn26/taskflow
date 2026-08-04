package protocol

import (
	"context"
	"encoding/hex"

	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
)

// Metadata keys carrying connection identity. They replace the HELLO
// handshake: gRPC sends them on every RPC, so identity no longer depends on
// connection-local state established by an earlier message.
const (
	MetadataRole    = "taskwire-role"
	MetadataOwnerID = "taskwire-owner-id"
)

// Roles recognized by the control plane.
const (
	RoleRuntime = "runtime"
	RoleWorker  = "worker"
	RoleAdmin   = "admin"
)

// methodRoles lists the roles permitted to call each RPC. Method names are
// the full gRPC paths the interceptors receive.
var methodRoles = map[string][]string{
	"/taskwire.v1.TaskwireControl/Submit":       {RoleRuntime},
	"/taskwire.v1.TaskwireControl/WatchResults": {RoleRuntime},
	"/taskwire.v1.TaskwireControl/AckResult":    {RoleRuntime},
	"/taskwire.v1.TaskwireControl/Cancel":       {RoleRuntime},
	"/taskwire.v1.TaskwireControl/QueryTasks":   {RoleRuntime},
	"/taskwire.v1.TaskwireControl/Work":         {RoleWorker},
	"/taskwire.v1.TaskwireControl/PutObject":    {RoleRuntime, RoleWorker},
	"/taskwire.v1.TaskwireControl/GetObject":    {RoleRuntime, RoleWorker},
	"/taskwire.v1.TaskwireControl/Status":       {RoleAdmin, RoleRuntime, RoleWorker},
}

// CallerIdentity is the per-RPC identity decoded from request metadata.
type CallerIdentity struct {
	Role    string
	OwnerID []byte
}

type callerKey struct{}

// CallerFrom returns the identity attached by the role interceptors.
func CallerFrom(ctx context.Context) (CallerIdentity, bool) {
	caller, ok := ctx.Value(callerKey{}).(CallerIdentity)
	return caller, ok
}

// authorize decodes and validates caller identity for method.
func authorize(ctx context.Context, method string) (context.Context, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, StatusError(NotRegistered, "request carries no metadata")
	}

	roles := md.Get(MetadataRole)
	if len(roles) != 1 || roles[0] == "" {
		return nil, StatusError(NotRegistered, "request has no "+MetadataRole+" metadata")
	}
	role := roles[0]

	allowed, known := methodRoles[method]
	if !known {
		return nil, StatusError(RoleForbidden, "unknown method")
	}
	permitted := false
	for _, candidate := range allowed {
		if candidate == role {
			permitted = true
			break
		}
	}
	if !permitted {
		return nil, StatusError(RoleForbidden, "role "+role+" may not call "+method)
	}

	caller := CallerIdentity{Role: role}
	if role == RoleRuntime {
		values := md.Get(MetadataOwnerID)
		if len(values) != 1 {
			return nil, StatusError(InvalidMessage, "runtime role requires "+MetadataOwnerID)
		}
		ownerID, err := hex.DecodeString(values[0])
		if err != nil || len(ownerID) != 16 {
			return nil, StatusError(InvalidMessage, MetadataOwnerID+" must be 16 hex-encoded bytes")
		}
		caller.OwnerID = ownerID
	}

	return context.WithValue(ctx, callerKey{}, caller), nil
}

// UnaryRoleInterceptor enforces per-method role authorization on unary RPCs.
func UnaryRoleInterceptor() grpc.UnaryServerInterceptor {
	return func(
		ctx context.Context,
		req interface{},
		info *grpc.UnaryServerInfo,
		handler grpc.UnaryHandler,
	) (interface{}, error) {
		authorized, err := authorize(ctx, info.FullMethod)
		if err != nil {
			return nil, err
		}
		return handler(authorized, req)
	}
}

// StreamRoleInterceptor enforces per-method role authorization on streaming
// RPCs.
func StreamRoleInterceptor() grpc.StreamServerInterceptor {
	return func(
		srv interface{},
		stream grpc.ServerStream,
		info *grpc.StreamServerInfo,
		handler grpc.StreamHandler,
	) error {
		authorized, err := authorize(stream.Context(), info.FullMethod)
		if err != nil {
			return err
		}
		return handler(srv, &authorizedStream{ServerStream: stream, ctx: authorized})
	}
}

type authorizedStream struct {
	grpc.ServerStream
	ctx context.Context
}

func (s *authorizedStream) Context() context.Context { return s.ctx }
