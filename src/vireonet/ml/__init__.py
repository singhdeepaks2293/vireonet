"""Machine learning for network operations (advisory only; never acts on its own).

- Unsupervised: anomaly detection on interface traffic (Isolation Forest,
  Local Outlier Factor, z-score baseline); k-means clustering of interface
  traffic profiles (k chosen by silhouette score).
- Supervised classification: change failure risk (gradient boosting, random
  forest, logistic regression).
- Supervised regression: change duration and next-hour traffic forecast
  (linear, ridge, lasso, random forest, gradient boosting, k-nearest
  neighbours, support vector regression).
- Reinforcement learning: tabular Q-learning for rollout wave size.
"""
