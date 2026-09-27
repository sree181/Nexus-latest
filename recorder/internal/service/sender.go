package service

import (
	"context"
	"log"
	"math"
	"time"

	"github.com/sree181/Nexus-latest/recorder/internal/cryptoqueue"
	"github.com/sree181/Nexus-latest/recorder/internal/protocol"
)

type Poster interface {
	Post(context.Context, protocol.Envelope) (map[string]any, error)
}

type Sender struct {
	Store   *cryptoqueue.Store
	Client  Poster
	Wake    <-chan struct{}
	Timeout time.Duration
	Poll    time.Duration
	Logger  *log.Logger
}

func (s *Sender) Run(ctx context.Context) error {
	if s.Poll <= 0 {
		s.Poll = 10 * time.Second
	}
	if s.Timeout <= 0 {
		s.Timeout = 2 * time.Second
	}
	ticker := time.NewTicker(s.Poll)
	defer ticker.Stop()
	for {
		if err := s.DrainOnce(ctx); err != nil && s.Logger != nil {
			s.Logger.Printf("recorder delivery diagnostic=drain_failed error=%q", err.Error())
		}
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		case <-s.Wake:
		}
	}
}

func (s *Sender) DrainOnce(ctx context.Context) error {
	keys, err := s.Store.ReadySessions(ctx)
	if err != nil {
		return err
	}
	for _, key := range keys {
		if err := s.Store.EnsureOpeningQueued(ctx, key); err != nil {
			return err
		}
		for {
			record, err := s.Store.LeaseNext(ctx, key, 30*time.Second)
			if err != nil {
				return err
			}
			if record == nil {
				break
			}
			requestCtx, cancel := context.WithTimeout(ctx, s.Timeout)
			response, postErr := s.Client.Post(requestCtx, record.Envelope)
			cancel()
			if postErr != nil {
				if protocol.IsTrustError(postErr) {
					if blockErr := s.Store.Block(ctx, *record); blockErr != nil {
						return blockErr
					}
					if s.Logger != nil {
						s.Logger.Printf("recorder delivery diagnostic=blocked_trust kind=%s", record.Envelope.Kind)
					}
					break
				}
				if protocol.IsPermanentValidationError(postErr) {
					if blockErr := s.Store.Block(ctx, *record); blockErr != nil {
						return blockErr
					}
					if s.Logger != nil {
						s.Logger.Printf("recorder delivery diagnostic=blocked_validation kind=%s status=%q", record.Envelope.Kind, postErr.Error())
					}
					break
				}
				delay := retryDelay(record.Attempts + 1)
				if releaseErr := s.Store.Release(ctx, *record, delay); releaseErr != nil {
					return releaseErr
				}
				if s.Logger != nil {
					s.Logger.Printf("recorder delivery diagnostic=retained kind=%s attempts=%d", record.Envelope.Kind, record.Attempts+1)
				}
				break
			}
			if err := s.Store.Acknowledge(ctx, *record, response); err != nil {
				return err
			}
		}
	}
	return nil
}

func retryDelay(attempt int) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	seconds := math.Pow(2, float64(attempt-1))
	if seconds > 300 {
		seconds = 300
	}
	return time.Duration(seconds * float64(time.Second))
}
