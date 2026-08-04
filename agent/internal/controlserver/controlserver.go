// Package controlserver implements the Phase 1 agent control plane: a gRPC
// service on a Unix domain socket. It enforces role authorization and the
// semantic validation rules, and answers Status. It intentionally does not
// implement task execution, storage, or scheduling — those are later phases.
// AgentHarness uses it to prove process lifecycle, readiness, and the
// protocol contract.
package controlserver

import (
	"context"
	"errors"
	"io"
	"net"
	"os"

	"github.com/sanketn26/taskwire/agent/pkg/protocol"
	"github.com/sanketn26/taskwire/agent/pkg/protocol/pb"
	"google.golang.org/grpc"
)

// Server serves the TaskwireControl service over a Unix domain socket.
type Server struct {
	pb.UnimplementedTaskwireControlServer

	version  string
	listener net.Listener
	grpc     *grpc.Server
}

// New creates a Server bound to socketPath. The socket file is removed first
// if a stale one is present, since a clean shutdown always removes it.
// maxMessageBytes bounds both directions, replacing the frame-size limit.
func New(socketPath, version string, maxMessageBytes int) (*Server, error) {
	if err := os.Remove(socketPath); err != nil && !os.IsNotExist(err) {
		return nil, err
	}

	l, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, err
	}

	s := &Server{
		version:  version,
		listener: l,
		grpc: grpc.NewServer(
			grpc.MaxRecvMsgSize(maxMessageBytes),
			grpc.MaxSendMsgSize(maxMessageBytes),
			grpc.UnaryInterceptor(protocol.UnaryRoleInterceptor()),
			grpc.StreamInterceptor(protocol.StreamRoleInterceptor()),
		),
	}
	pb.RegisterTaskwireControlServer(s.grpc, s)
	return s, nil
}

// Serve accepts connections until the server is stopped. It returns nil on a
// clean shutdown.
func (s *Server) Serve() error {
	if err := s.grpc.Serve(s.listener); err != nil && !errors.Is(err, grpc.ErrServerStopped) {
		return err
	}
	return nil
}

// Status returns the agent status snapshot. It is the readiness probe used by
// the CLI and the Python test harness.
func (s *Server) Status(ctx context.Context, _ *pb.StatusRequest) (*pb.StatusSnapshot, error) {
	return &pb.StatusSnapshot{
		Version:            s.version,
		Pid:                uint64(os.Getpid()),
		Ready:              true,
		TaskCounts:         map[string]uint64{},
		ActiveLeases:       0,
		WorkerPids:         []uint64{},
		WorkerRestarts:     0,
		StorageHealthy:     true,
		ClusterMembers:     0,
		KafkaOutboxPending: 0,
		LastErrorCode:      nil,
	}, nil
}

// Work validates the worker session handshake and capability registration.
// Leasing itself arrives in a later phase, so the stream stays open, draining
// and validating worker messages until the client closes it.
func (s *Server) Work(stream pb.TaskwireControl_WorkServer) error {
	state := newWorkerState()

	first, err := stream.Recv()
	if err != nil {
		if errors.Is(err, io.EOF) {
			return nil
		}
		return err
	}

	registration := first.GetRegister()
	if registration == nil {
		return protocol.StatusError(protocol.NotRegistered,
			"first Work message must be a registration")
	}
	if err := state.register(registration); err != nil {
		return protocol.StatusFromError(err)
	}

	if err := stream.Send(&pb.AgentMessage{
		Body: &pb.AgentMessage_Registered{Registered: &pb.WorkerRegistrationResponse{
			WorkerId:   state.WorkerID,
			Generation: state.CapabilityGeneration,
			Accepted:   uint32(len(registration.GetTasks().GetTasks())),
		}},
	}); err != nil {
		return err
	}

	for {
		message, err := stream.Recv()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		if err := s.handleWorkerMessage(stream, state, message); err != nil {
			return err
		}
	}
}

func (s *Server) handleWorkerMessage(
	stream pb.TaskwireControl_WorkServer,
	state *workerState,
	message *pb.WorkerMessage,
) error {
	switch body := message.Body.(type) {
	case *pb.WorkerMessage_UpdateTasks:
		if err := state.updateTasks(body.UpdateTasks); err != nil {
			return protocol.StatusFromError(err)
		}
		return stream.Send(&pb.AgentMessage{
			Body: &pb.AgentMessage_Registered{Registered: &pb.WorkerRegistrationResponse{
				WorkerId:   state.WorkerID,
				Generation: state.CapabilityGeneration,
				Accepted:   uint32(len(body.UpdateTasks.GetTasks())),
			}},
		})

	case *pb.WorkerMessage_Register:
		return protocol.StatusError(protocol.InvalidMessage,
			"worker registration may only be sent as the first Work message")

	case *pb.WorkerMessage_Pull:
		if err := state.validatePull(body.Pull); err != nil {
			return protocol.StatusFromError(err)
		}
		// No queue yet: a phase 1 agent has nothing to lease, and gRPC lets
		// the stream stay open until a task exists.
		return nil

	case *pb.WorkerMessage_Heartbeat:
		if err := protocol.Validate(body.Heartbeat); err != nil {
			return protocol.StatusFromError(err)
		}
		return stream.Send(&pb.AgentMessage{
			Body: &pb.AgentMessage_HeartbeatAck{HeartbeatAck: &pb.HeartbeatResponse{
				LeaseId: body.Heartbeat.GetLeaseId(),
			}},
		})

	case *pb.WorkerMessage_Complete:
		if err := protocol.Validate(body.Complete); err != nil {
			return protocol.StatusFromError(err)
		}
		return stream.Send(&pb.AgentMessage{
			Body: &pb.AgentMessage_CompleteAck{CompleteAck: &pb.CompletionResponse{
				LeaseId: body.Complete.GetLeaseId(),
			}},
		})

	default:
		return protocol.StatusError(protocol.InvalidMessage, "empty worker message body")
	}
}

// Close stops accepting new connections, waits for in-flight handlers, and
// removes the socket file so no owned socket is left behind.
func (s *Server) Close() error {
	address := s.listener.Addr().String()
	s.grpc.GracefulStop()
	_ = os.Remove(address)
	return nil
}
