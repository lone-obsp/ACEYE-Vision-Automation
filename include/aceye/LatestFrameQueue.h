#pragma once

#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <optional>
#include <utility>

namespace aceye {

// A one-slot latest-value hand-off. Slow consumers never create an unbounded
// backlog: publishing a new frame replaces the previous pending frame.
template <typename T>
class LatestFrameQueue {
public:
    void publish(T value) {
        {
            std::lock_guard lock(mutex_);
            latest_ = std::move(value);
        }
        condition_.notify_one();
    }

    [[nodiscard]] std::optional<T> try_consume() {
        std::lock_guard lock(mutex_);
        if (!latest_) {
            return std::nullopt;
        }
        std::optional<T> value = std::move(latest_);
        latest_.reset();
        return value;
    }

    [[nodiscard]] std::size_t pending() const {
        std::lock_guard lock(mutex_);
        return latest_.has_value() ? 1U : 0U;
    }

private:
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    std::optional<T> latest_;
};

} // namespace aceye
