import pytest

import gym
import torch as th
from torch.distributions import Normal

from torchy_baselines import A2C, TD3
from torchy_baselines.common.vec_env import DummyVecEnv, VecNormalize
from torchy_baselines.common.monitor import Monitor


def test_state_dependent_exploration():
    """
    Check that the gradient correspond to the expected one
    """
    n_states = 2
    state_dim = 3
    action_dim = 10
    sigma_hat = th.ones(state_dim, action_dim, requires_grad=True)
    # Reduce the number of parameters
    # sigma_ = th.ones(state_dim, action_dim) * sigma_

    # weights_dist = Normal(th.zeros_like(log_sigma), th.exp(log_sigma))
    th.manual_seed(2)
    weights_dist = Normal(th.zeros_like(sigma_hat), sigma_hat)
    weights = weights_dist.rsample()

    state = th.rand(n_states, state_dim)
    mu = th.ones(action_dim)
    # print(weights.shape, state.shape)
    noise = th.mm(state, weights)

    action = mu + noise

    variance = th.mm(state ** 2, sigma_hat ** 2)
    action_dist = Normal(mu, th.sqrt(variance))

    # Sum over the action dimension because we assume they are independent
    loss = action_dist.log_prob((action).detach()).sum(dim=-1).mean()
    loss.backward()

    # From Rueckstiess paper: check that the computed gradient
    # correspond to the analytical form
    grad = th.zeros_like(sigma_hat)
    for j in range(action_dim):
        # sigma_hat is the std of the gaussian distribution of the noise matrix weights
        # sigma_j = sum_j(state_i **2 * sigma_hat_ij ** 2)
        # sigma_j is the standard deviation of the policy gaussian distribution
        sigma_j = th.sqrt(variance[:, j])
        for i in range(state_dim):
            # Derivative of the log probability of the jth component of the action
            # w.r.t. the standard deviation sigma_j
            d_log_policy_j = (noise[:, j] ** 2 - sigma_j ** 2) / sigma_j ** 3
            # Derivative of sigma_j w.r.t. sigma_hat_ij
            d_log_sigma_j = (state[:, i] ** 2 * sigma_hat[i, j]) / sigma_j
            # Chain rule, average over the minibatch
            grad[i, j] = (d_log_policy_j * d_log_sigma_j).mean()

    # sigma.grad should be equal to grad
    assert sigma_hat.grad.allclose(grad)


@pytest.mark.parametrize("model_class", [A2C])
@pytest.mark.parametrize("sde_net_arch", [None, [32, 16]])
def test_state_dependent_noise(model_class, sde_net_arch):
    env_id = 'MountainCarContinuous-v0'

    env = VecNormalize(DummyVecEnv([lambda: Monitor(gym.make(env_id))]), norm_reward=True)
    eval_env = VecNormalize(DummyVecEnv([lambda: Monitor(gym.make(env_id))]), training=False, norm_reward=False)

    model = model_class('MlpPolicy', env, n_steps=200, use_sde=True, ent_coef=0.00, verbose=1, learning_rate=3e-4,
                        policy_kwargs=dict(log_std_init=0.0, ortho_init=False, sde_net_arch=sde_net_arch), seed=None)
    model.learn(total_timesteps=int(1000), log_interval=5, eval_freq=500, eval_env=eval_env)


@pytest.mark.parametrize("model_class", [TD3])
def test_state_dependent_offpolicy_noise(model_class):
    model = model_class('MlpPolicy', 'Pendulum-v0', use_sde=True, seed=None, create_eval_env=True,
                        verbose=1, policy_kwargs=dict(log_std_init=-2))
    model.learn(total_timesteps=int(1000), eval_freq=500)


def test_scheduler():
    def scheduler(progress):
        return -2.0 * progress + 1

    model = TD3('MlpPolicy', 'Pendulum-v0', use_sde=True, seed=None, create_eval_env=True,
                        verbose=1, sde_log_std_scheduler=scheduler)
    model.learn(total_timesteps=int(1000), eval_freq=500)
    assert th.isclose(model.actor.log_std, th.ones_like(model.actor.log_std)).all()
