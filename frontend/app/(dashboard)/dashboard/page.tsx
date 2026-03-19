// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { mockUser, mockDashboardStats } from "@/lib/mockData"
import { authConfig } from "@/lib/auth-config"

const IS_DEV_MODE = process.env.DEV_MODE === "true"

export default async function DashboardPage() {
  let user, stats

  if (IS_DEV_MODE) {
    user = mockUser
    stats = mockDashboardStats
  } else {
    const tokens = await getTokens(await cookies(), authConfig)
    const decodedToken = tokens?.decodedToken
    user = {
      name: decodedToken?.name || decodedToken?.email,
      email: decodedToken?.email,
      image: decodedToken?.picture,
    }
    stats = { totalPatients: 0, activePatients: 0, sessionsThisMonth: 0, recentActivity: [] }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-bold text-neutral-900">
          Welcome back, {user?.name?.split(" ")[0]}
        </h1>
        <p className="text-neutral-600 mt-2">
          Manage your therapy sessions and patient information
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="card">
          <h3 className="text-sm font-medium text-neutral-500">Total Patients</h3>
          <p className="text-3xl font-bold text-primary-600 mt-2">{stats.totalPatients}</p>
        </div>

        <div className="card">
          <h3 className="text-sm font-medium text-neutral-500">Active Patients</h3>
          <p className="text-3xl font-bold text-secondary-600 mt-2">{stats.activePatients}</p>
        </div>

        <div className="card">
          <h3 className="text-sm font-medium text-neutral-500">Sessions This Month</h3>
          <p className="text-3xl font-bold text-accent-600 mt-2">{stats.sessionsThisMonth}</p>
        </div>
      </div>

      <div className="card">
        <h2 className="text-xl font-display font-semibold text-neutral-900 mb-4">
          Recent Activity
        </h2>
        {stats.recentActivity.length > 0 ? (
          <div className="space-y-4">
            {stats.recentActivity.map((activity) => (
              <div
                key={activity.id}
                className="flex items-start gap-3 p-3 rounded-lg hover:bg-neutral-50 transition-colors"
              >
                <div className="flex-1">
                  <p className="text-sm text-neutral-900">{activity.description}</p>
                  <p className="text-xs text-neutral-500 mt-1">
                    {new Date(activity.timestamp).toLocaleString()}
                  </p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-neutral-500 text-center py-12">
            No recent activity to display
          </p>
        )}
      </div>
    </div>
  )
}
