import { db, auth, call } from './client';

type LoggedUserResponse = string | null;

interface UserDoc {
  name: string;
  full_name: string;
}

export const getLoggedUser = async (): Promise<LoggedUserResponse> => {
  try {
    const response = await auth.getLoggedInUser();
    return response as LoggedUserResponse;
  } catch (error) {
    console.error('Error getting logged user:', error);
    return null;
  }
};

export const getUserRoles = async (email: string): Promise<{ roles: string[]; full_name: string }> => {
  try {
    const userDoc = await db.getDoc<UserDoc>('User', email);
    const rolesResponse = await call.get<{ message: string[] }>(
      'frappe.core.doctype.user.user.get_roles',
      { uid: email }
    );

    if (!userDoc || !Array.isArray(rolesResponse.message)) {
      return { roles: [], full_name: '' };
    }

    return {
      roles: rolesResponse.message,
      full_name: userDoc.full_name
    };
  } catch (error) {
    console.error('Error getting user details:', error);
    return { roles: [], full_name: '' };
  }
};

export const logout = async () => {
  try {
    return auth.logout();
  }catch(e){
    console.error('Error logging out:', e);
    return false;
  }
}
