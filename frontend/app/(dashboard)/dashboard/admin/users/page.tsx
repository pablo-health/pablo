// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * User Management Page (Admin)
 *
 * Displays list of all users with ability to disable/enable accounts.
 * Also manages the email allowlist for new user registrations.
 */

"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  listUsers,
  disableUser,
  enableUser,
  listAllowlist,
  addToAllowlist,
  removeFromAllowlist,
} from "@/lib/api/admin"
import { queryKeys } from "@/lib/api/queryKeys"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { AlertCircle, CheckCircle, Shield, Trash2, UserPlus } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"

export default function UserManagementPage() {
  const queryClient = useQueryClient()
  const [emailToAdd, setEmailToAdd] = useState("")
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [emailToRemove, setEmailToRemove] = useState<string | null>(null)

  // Fetch users
  const {
    data: usersData,
    isLoading: usersLoading,
    error: usersError,
  } = useQuery({
    queryKey: queryKeys.admin.users(),
    queryFn: () => listUsers(),
  })

  // Fetch allowlist
  const {
    data: allowlistData,
    isLoading: allowlistLoading,
    error: allowlistError,
  } = useQuery({
    queryKey: queryKeys.admin.allowlist(),
    queryFn: () => listAllowlist(),
  })

  // Disable user mutation
  const disableUserMutation = useMutation({
    mutationFn: (userId: string) => disableUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.users() })
    },
  })

  // Enable user mutation
  const enableUserMutation = useMutation({
    mutationFn: (userId: string) => enableUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.users() })
    },
  })

  // Add to allowlist mutation
  const addToAllowlistMutation = useMutation({
    mutationFn: (email: string) => addToAllowlist(email),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.allowlist() })
      setEmailToAdd("")
    },
  })

  // Remove from allowlist mutation
  const removeFromAllowlistMutation = useMutation({
    mutationFn: (email: string) => removeFromAllowlist(email),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.admin.allowlist() })
      setDeleteDialogOpen(false)
      setEmailToRemove(null)
    },
  })

  const handleToggleUserStatus = (userId: string, currentStatus: string) => {
    if (currentStatus === "approved") {
      disableUserMutation.mutate(userId)
    } else if (currentStatus === "disabled") {
      enableUserMutation.mutate(userId)
    }
  }

  const handleAddToAllowlist = (e: React.FormEvent) => {
    e.preventDefault()
    if (emailToAdd.trim()) {
      addToAllowlistMutation.mutate(emailToAdd.trim())
    }
  }

  const handleRemoveClick = (email: string) => {
    setEmailToRemove(email)
    setDeleteDialogOpen(true)
  }

  const handleConfirmRemove = () => {
    if (emailToRemove) {
      removeFromAllowlistMutation.mutate(emailToRemove)
    }
  }

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "N/A"
    try {
      return new Date(dateString).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    } catch {
      return "N/A"
    }
  }

  const getStatusBadge = (status: string) => {
    if (status === "approved") {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-secondary-100 text-secondary-700">
          <CheckCircle className="w-3 h-3" />
          approved
        </span>
      )
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-red-100 text-red-700">
        <AlertCircle className="w-3 h-3" />
        {status}
      </span>
    )
  }

  const getMfaBadge = (mfaEnrolledAt: string | null) => {
    if (mfaEnrolledAt) {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-secondary-100 text-secondary-700">
          <CheckCircle className="w-3 h-3" />
          Enrolled
        </span>
      )
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-amber-100 text-amber-700">
          Not enrolled
        </span>
    )
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold text-neutral-900">
          User Management
        </h1>
        <p className="text-neutral-600 mt-2">
          Manage user accounts and email allowlist
        </p>
      </div>

      {/* Users Section */}
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-neutral-900">All Users</h2>

        {/* Loading State */}
        {usersLoading && <Skeleton className="h-96 w-full" />}

        {/* Error State */}
        {usersError && !usersLoading && (
          <div className="card p-12">
            <div className="text-center space-y-4">
              <AlertCircle className="h-12 w-12 text-red-500 mx-auto" />
              <h3 className="text-xl font-semibold text-neutral-900">
                Failed to load users
              </h3>
              <p className="text-neutral-600">
                {usersError instanceof Error
                  ? usersError.message
                  : "An error occurred"}
              </p>
            </div>
          </div>
        )}

        {/* Users Table */}
        {!usersLoading && !usersError && usersData && (
          <div className="card">
            {usersData.data.length === 0 ? (
              <div className="text-center py-12">
                <p className="text-neutral-500">No users found</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Email</TableHead>
                      <TableHead>Name</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Admin</TableHead>
                      <TableHead>MFA</TableHead>
                      <TableHead>BAA</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {usersData.data.map((user) => (
                      <TableRow key={user.id}>
                        <TableCell className="font-medium">
                          {user.email}
                        </TableCell>
                        <TableCell>{user.name}</TableCell>
                        <TableCell>{getStatusBadge(user.status)}</TableCell>
                        <TableCell>
                          {user.is_admin && (
                            <Shield className="w-4 h-4 text-primary-600" />
                          )}
                        </TableCell>
                        <TableCell>{getMfaBadge(user.mfa_enrolled_at)}</TableCell>
                        <TableCell>
                          {user.baa_accepted_at ? (
                            <CheckCircle className="w-4 h-4 text-secondary-600" />
                          ) : (
                            <span className="text-neutral-400 text-sm">
                              Not accepted
                            </span>
                          )}
                        </TableCell>
                        <TableCell>{formatDate(user.created_at)}</TableCell>
                        <TableCell className="text-right">
                          {!user.is_admin && (
                            <Button
                              variant={
                                user.status === "approved"
                                  ? "outline"
                                  : "default"
                              }
                              size="sm"
                              onClick={() =>
                                handleToggleUserStatus(user.id, user.status)
                              }
                              disabled={
                                disableUserMutation.isPending ||
                                enableUserMutation.isPending
                              }
                            >
                              {user.status === "approved" ? "Disable" : "Enable"}
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}

        {!usersLoading && !usersError && usersData && (
          <p className="text-sm text-neutral-500">
            Total: {usersData.total} {usersData.total === 1 ? "user" : "users"}
          </p>
        )}
      </div>

      {/* Allowlist Section */}
      <div className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold text-neutral-900">
            Email Allowlist
          </h2>
          <p className="text-neutral-600 text-sm mt-1">
            Only emails on this list can register for new accounts
          </p>
        </div>

        {/* Add Email Form */}
        <form onSubmit={handleAddToAllowlist} className="card p-4">
          <div className="flex gap-3">
            <div className="flex-1">
              <Input
                type="email"
                placeholder="email@example.com"
                value={emailToAdd}
                onChange={(e) => setEmailToAdd(e.target.value)}
                required
              />
            </div>
            <Button
              type="submit"
              className="btn-primary"
              disabled={addToAllowlistMutation.isPending || !emailToAdd.trim()}
            >
              <UserPlus className="w-4 h-4 mr-2" />
              {addToAllowlistMutation.isPending ? "Adding..." : "Add Email"}
            </Button>
          </div>
        </form>

        {/* Loading State */}
        {allowlistLoading && <Skeleton className="h-64 w-full" />}

        {/* Error State */}
        {allowlistError && !allowlistLoading && (
          <div className="card p-12">
            <div className="text-center space-y-4">
              <AlertCircle className="h-12 w-12 text-red-500 mx-auto" />
              <h3 className="text-xl font-semibold text-neutral-900">
                Failed to load allowlist
              </h3>
              <p className="text-neutral-600">
                {allowlistError instanceof Error
                  ? allowlistError.message
                  : "An error occurred"}
              </p>
            </div>
          </div>
        )}

        {/* Allowlist Table */}
        {!allowlistLoading && !allowlistError && allowlistData && (
          <div className="card">
            {allowlistData.data.length === 0 ? (
              <div className="text-center py-12">
                <p className="text-neutral-500">
                  No emails in allowlist. Add emails above to allow new user
                  registrations.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Email</TableHead>
                      <TableHead>Added By</TableHead>
                      <TableHead>Added Date</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {allowlistData.data.map((entry) => (
                      <TableRow key={entry.email}>
                        <TableCell className="font-medium">
                          {entry.email}
                        </TableCell>
                        <TableCell>{entry.added_by}</TableCell>
                        <TableCell>{formatDate(entry.added_at)}</TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleRemoveClick(entry.email)}
                            disabled={removeFromAllowlistMutation.isPending}
                          >
                            <Trash2 className="w-4 h-4 text-red-500" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}

        {!allowlistLoading && !allowlistError && allowlistData && (
          <p className="text-sm text-neutral-500">
            Total: {allowlistData.total}{" "}
            {allowlistData.total === 1 ? "email" : "emails"}
          </p>
        )}
      </div>

      {/* Remove Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove Email from Allowlist</DialogTitle>
            <DialogDescription>
              Are you sure you want to remove{" "}
              <strong>{emailToRemove}</strong> from the allowlist? This will
              prevent new users with this email from registering.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
              disabled={removeFromAllowlistMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmRemove}
              disabled={removeFromAllowlistMutation.isPending}
            >
              {removeFromAllowlistMutation.isPending ? "Removing..." : "Remove"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
